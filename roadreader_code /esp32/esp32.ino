/*
 * Road Reader - ESP32 firmware (ICM-42688 over SPI + BLE to the Road Reader iPhone app)
 *
 * Advertises as "RoadReader" with the Nordic UART Service. Once the phone connects and taps
 * "Start Readings", every IMU sample is sent as one newline-terminated CSV line:
 *
 *     t_ms,ax,ay,az,gx,gy,gz\n   ESP32 millis(), accel in g, gyro in deg/s
 *
 * Sampled at 200 Hz and packed up to 3 lines per BLE notification (the app splits on '\n').
 * Why 200 Hz: a bike frame rings at ~28-40 Hz.  At 50 Hz that vibration aliases into fake
 * road frequencies and the road-profile reconstruction falls apart (see software/roadimu).
 * The timestamp lets the processing place every sample exactly, even if a packet is lost.
 *
 * The app appends the "Crack/Pothole Detected" label (column 7), GPS and timestamps itself.
 * The app writes "START" / "STOP" to the RX characteristic; both are honoured below.
 *
 * LED (GPIO 2): blinking = advertising, solid = phone connected.
 */

#define LED 2

#include <SPI.h>
#include "ICM42688.h"
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

#define IMU_CS_PIN 5

// --- BLE identity: must match roadreader_app (Settings > Bluetooth) ---
#define DEVICE_NAME  "RoadReader"
#define SERVICE_UUID "6E400001-B5A3-F393-E0A9-E50E24DC4CA1"
#define RX_UUID      "6E400002-B5A3-F393-E0A9-E50E24DC4CA1"   // phone -> ESP32 (START/STOP)
#define TX_UUID      "6E400003-B5A3-F393-E0A9-E50E24DC4CA1"   // ESP32 -> phone (CSV rows)

static const uint32_t SAMPLE_PERIOD_US = 5000;  // 200 Hz
static const size_t   MAX_PAYLOAD      = 180;   // bytes per notification (MTU 185 - 3)
static const int      LINES_PER_PACKET = 3;
static const uint32_t SERIAL_PERIOD_MS = 500;   // debug print rate on the serial monitor
static const uint32_t BLINK_PERIOD_MS  = 500;

ICM42688 IMU(SPI, IMU_CS_PIN);

BLECharacteristic* txChar = nullptr;
volatile bool phoneConnected = false;
volatile bool streaming = true;   // default on so the phone gets data even if START is missed

unsigned long nextSampleUs = 0;
unsigned long lastSerial = 0;
unsigned long lastBlink = 0;
unsigned long rowsSent = 0;

char batch[MAX_PAYLOAD + 1];
size_t batchLen = 0;
int batchLines = 0;

void flushBatch()
{
    if (batchLen > 0 && phoneConnected && streaming && txChar)
    {
        txChar->setValue((uint8_t*)batch, batchLen);
        txChar->notify();
    }
    batchLen = 0;
    batchLines = 0;
}

class ServerCallbacks : public BLEServerCallbacks
{
    void onConnect(BLEServer*) override
    {
        phoneConnected = true;
        digitalWrite(LED, HIGH);
        Serial.println("Phone connected");
    }
    void onDisconnect(BLEServer* server) override
    {
        phoneConnected = false;
        Serial.println("Phone disconnected, advertising again");
        server->startAdvertising();
    }
};

class RxCallbacks : public BLECharacteristicCallbacks
{
    void onWrite(BLECharacteristic* c) override
    {
        // getValue() is std::string on ESP32 core 2.x and String on 3.x; c_str() works for both.
        String cmd = String(c->getValue().c_str());
        cmd.trim();
        if (cmd.equalsIgnoreCase("START"))
        {
            streaming = true;
            Serial.println("START received");
        }
        else if (cmd.equalsIgnoreCase("STOP"))
        {
            streaming = false;
            Serial.println("STOP received");
        }
    }
};

void setupBLE()
{
    BLEDevice::init(DEVICE_NAME);
    BLEDevice::setMTU(185);   // lets a whole CSV row fit in one notification

    BLEServer* server = BLEDevice::createServer();
    server->setCallbacks(new ServerCallbacks());

    BLEService* service = server->createService(SERVICE_UUID);

    txChar = service->createCharacteristic(TX_UUID, BLECharacteristic::PROPERTY_NOTIFY);
    txChar->addDescriptor(new BLE2902());   // required so iOS can enable notifications

    BLECharacteristic* rxChar = service->createCharacteristic(
        RX_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
    rxChar->setCallbacks(new RxCallbacks());

    service->start();

    BLEAdvertising* adv = BLEDevice::getAdvertising();
    adv->addServiceUUID(SERVICE_UUID);   // the app auto-connects to this service even if the name changes
    adv->setScanResponse(true);
    adv->setMinPreferred(0x06);
    adv->setMinPreferred(0x12);
    BLEDevice::startAdvertising();

    Serial.println("BLE advertising as " DEVICE_NAME);
}

void setup()
{
    Serial.begin(115200);
    unsigned long t0 = millis();
    while (!Serial && millis() - t0 < 2000)   // don't hang when running on battery without USB
        ;

    pinMode(LED, OUTPUT);
    digitalWrite(LED, LOW);

    Serial.println("Initializing Teyleten Robot ICM42688...");
    int status = IMU.begin();
    if (status < 0)
    {
        Serial.print("IMU initialization failed! Error code: ");
        Serial.println(status);
        while (1)
        {
            digitalWrite(LED, !digitalRead(LED));   // fast blink = IMU fault
            delay(100);
        }
    }
    Serial.println("ICM42688 ready");

    setupBLE();
}

void loop()
{
    unsigned long now = millis();

    // Blink while waiting for the phone.
    if (!phoneConnected && now - lastBlink >= BLINK_PERIOD_MS)
    {
        lastBlink = now;
        digitalWrite(LED, !digitalRead(LED));
    }

    // Fixed-rate schedule on micros() so the rate doesn't drift; if we fall far behind, resync.
    unsigned long nowUs = micros();
    if ((long)(nowUs - nextSampleUs) < 0)
        return;
    nextSampleUs += SAMPLE_PERIOD_US;
    if ((long)(nowUs - nextSampleUs) > (long)(5 * SAMPLE_PERIOD_US))
        nextSampleUs = nowUs + SAMPLE_PERIOD_US;

    IMU.getAGT();
    float ax = IMU.accX();
    float ay = IMU.accY();
    float az = IMU.accZ();
    float gx = IMU.gyrX();
    float gy = IMU.gyrY();
    float gz = IMU.gyrZ();

    char line[96];
    int len = snprintf(line, sizeof(line), "%lu,%.4f,%.4f,%.4f,%.2f,%.2f,%.2f\n", now, ax, ay, az, gx, gy, gz);
    if (len <= 0 || len >= (int)sizeof(line))
        return;

    if (phoneConnected && streaming && txChar)
    {
        if (batchLen + len > MAX_PAYLOAD)
            flushBatch();
        memcpy(batch + batchLen, line, len);
        batchLen += len;
        rowsSent++;
        if (++batchLines >= LINES_PER_PACKET)
            flushBatch();
    }
    else
    {
        batchLen = 0;
        batchLines = 0;
    }

    if (now - lastSerial >= SERIAL_PERIOD_MS)
    {
        lastSerial = now;
        Serial.print(phoneConnected ? (streaming ? "[BLE streaming] " : "[BLE paused] ") : "[advertising] ");
        Serial.print("rows=");
        Serial.print(rowsSent);
        Serial.print("  ");
        Serial.print(line);
    }
}
