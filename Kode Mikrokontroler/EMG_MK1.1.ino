#include <Arduino.h>
#include <WiFi.h>
#include <driver/rtc_io.h>
#include <Preferences.h>

const int batteryPin = A0; 
const int EMGPin = A1;     
const int ledPin1 = D8;    
const int ledPin2 = D9;    
const int ledPin3 = D10;   
const int PMOSPin = D5;    
const int touchPin = D2; 

const int DEVICE_ID = 1;
const char* ssid = "BMEhost";
const char* password = "BMEhosting";
const uint16_t port = 8080;

const float wifiTimeoutSeconds = 30.0;
const unsigned long wifiTimeoutMs = (unsigned long)(wifiTimeoutSeconds * 1000);
const float transmissionFrequency = 50.0;
const long sendInterval = (long)(1000.0 / transmissionFrequency);

Preferences pref;
int srR = 0, srG = 0, srB = 12; 
int cnR = 0, cnG = 12, cnB = 0; 
bool syncSent = false; 

const float samplingFrequency = 2500.0;
const unsigned long intervalMicros = (unsigned long)(1000000.0 / samplingFrequency);
unsigned long previousMicros = 0;
float x[4], y[4], x_bsf[5], y_bsf[5], x_hpf[3], y_hpf[3];
float filteredEnvelope = 0;

float smoothedVbatt = -1.0; 

const float b[] = {0, 0.8005924035f, 1.6011848069f, 0.8005924035f};
const float a[] = {0, 1.0000000000f, 1.5610180758f, 0.6413515381f};
const float b_bsf[] = {0.991153595101663f, -3.770646770422277f, 5.568476159765916f, -3.770646770422277f, 0.991153595101663f};
const float a_bsf[] = {1.000000000000000f, -3.787399533082511f, 5.568397899355124f, -3.753894007762051f, 0.982385450614127f};
const float b_hpf[] = {0.914969144113082f, -1.829938288226165f, 0.914969144113082f};
const float a_hpf[] = {1.000000000000000f, -1.822694925196308f, 0.837181651256022f};

WiFiClient client;

bool systemActive = false;
bool greenLedDone = false;
unsigned long connectedMillis = 0;
unsigned long previousSendMillis = 0;
unsigned long lastBatteryMillis = 0;
unsigned long lastDisconnectTime = 0; 
const int dimIntensity = 12; 

void setLED(int r, int g, int b);
void updateMosfet(bool state);
void goToSleep();
void tryReconnect();
float Filter_lowpass(float inputVal);
float Filter_BSF(float inputVal);
float Filter_HPF(float inputVal);

void setup() {
  Serial.begin(115200);
  esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL);
  pinMode(ledPin1, OUTPUT);
  pinMode(ledPin2, OUTPUT);
  pinMode(ledPin3, OUTPUT);
  pinMode(touchPin, INPUT); 
  updateMosfet(false);

  pref.begin("bme_cfg", false);
  srR = pref.getInt("srR", 0); srG = pref.getInt("srG", 0); srB = pref.getInt("srB", dimIntensity);
  cnR = pref.getInt("cnR", 0); cnG = pref.getInt("cnG", 12); cnB = pref.getInt("cnB", 0);

  Serial.println("BME-> Body Muscle Evaluator");
  
  WiFi.begin(ssid, password);
  unsigned long startAttempt = millis();
  while (millis() - startAttempt < wifiTimeoutMs) {
    if (WiFi.status() == WL_CONNECTED) {
      Serial.println("\nWiFi Connected!");
      systemActive = true;
      connectedMillis = millis();
      setLED(cnR, cnG, cnB); 
      delay(2500); 
      return;
    }
    if ((millis() / 1000) % 2 == 0) setLED(srR, srG, srB);
    else setLED(0, 0, 0);
    delay(100);
  }
  goToSleep();
}

void loop() {
  unsigned long currentMillis = millis();
  unsigned long currentMicros = micros();

  if (currentMillis - lastBatteryMillis >= 1000) {
    lastBatteryMillis = currentMillis;
    
    uint32_t Vsum = 0;
    const int numSamples = 100;
    for(int i = 0; i < numSamples; i++) {
      Vsum += analogReadMilliVolts(batteryPin);
      delayMicroseconds(50);
    }
    
    float currentVbatt = (2.0 * Vsum / (float)numSamples) / 1000.0;

    if (smoothedVbatt < 0) {
      smoothedVbatt = currentVbatt;
    } else {
      smoothedVbatt = (0.9 * smoothedVbatt) + (0.1 * currentVbatt);
    }
    
    if (systemActive && WiFi.status() == WL_CONNECTED && client.connected()) {
      int percentage = (int)(constrain((smoothedVbatt - 3.2) * 100.0 / (4.2 - 3.2), 0, 100));
      client.print("B"); 
      client.print(DEVICE_ID);
      client.print(":");
      client.println(percentage);
      Serial.printf("Battery Sent: %d%% (Vsmooth: %.3f)\n", percentage, smoothedVbatt);
    }
  }

  if (systemActive && !greenLedDone) {
    if (currentMillis - connectedMillis >= 2000) {
      setLED(0, 0, 0);
      greenLedDone = true;
    }
  }
  
  if (systemActive && WiFi.status() != WL_CONNECTED) {
    updateMosfet(false);
    if (lastDisconnectTime == 0) lastDisconnectTime = currentMillis;
    if (currentMillis - lastDisconnectTime > 3000) { 
        lastDisconnectTime = 0; 
        tryReconnect(); 
    }
  }
  
  if (systemActive && WiFi.status() == WL_CONNECTED) {
    if (!client.connected()) {
      updateMosfet(false);
      client.connect(WiFi.gatewayIP(), port);
      syncSent = false;
    } else {
      updateMosfet(true);
        
      if (!syncSent) {
          client.print("SYNC");
          client.print(DEVICE_ID);
          client.print(":");
          client.printf("%d,%d,%d,%d,%d,%d\n", srR, srG, srB, cnR, cnG, cnB);
          syncSent = true;
      }

      if (client.available()) {
        String request = client.readStringUntil('\n');
        request.trim();
        
        if (request.startsWith("PWM1:")) analogWrite(ledPin1, request.substring(5).toInt());
        else if (request.startsWith("PWM2:")) analogWrite(ledPin2, request.substring(5).toInt());
        else if (request.startsWith("PWM3:")) analogWrite(ledPin3, request.substring(5).toInt());
        
        String cmd_srR = String(DEVICE_ID) + "_SR_R:";
        String cmd_srG = String(DEVICE_ID) + "_SR_G:";
        String cmd_srB = String(DEVICE_ID) + "_SR_B:";
        String cmd_cnR = String(DEVICE_ID) + "_CN_R:";
        String cmd_cnG = String(DEVICE_ID) + "_CN_G:";
        String cmd_cnB = String(DEVICE_ID) + "_CN_B:";

        if (request.startsWith(cmd_srR)) { srR = request.substring(cmd_srR.length()).toInt(); pref.putInt("srR", srR); }
        else if (request.startsWith(cmd_srG)) { srG = request.substring(cmd_srG.length()).toInt(); pref.putInt("srG", srG); }
        else if (request.startsWith(cmd_srB)) { srB = request.substring(cmd_srB.length()).toInt(); pref.putInt("srB", srB); }
        
        else if (request.startsWith(cmd_cnR)) { cnR = request.substring(cmd_cnR.length()).toInt(); pref.putInt("cnR", cnR); setLED(cnR, cnG, cnB); }
        else if (request.startsWith(cmd_cnG)) { cnG = request.substring(cmd_cnG.length()).toInt(); pref.putInt("cnG", cnG); setLED(cnR, cnG, cnB); }
        else if (request.startsWith(cmd_cnB)) { cnB = request.substring(cmd_cnB.length()).toInt(); pref.putInt("cnB", cnB); setLED(cnR, cnG, cnB); }
      }
      
      if (currentMicros - previousMicros >= intervalMicros) {
        previousMicros += intervalMicros;
        int rawValue = analogRead(EMGPin);
        float hpfValue = Filter_HPF((float)rawValue);
        float notchedValue = Filter_BSF(hpfValue);
        float rectifiedValue = fabs(notchedValue); 
        filteredEnvelope = Filter_lowpass(rectifiedValue);
      }
      
      if (currentMillis - previousSendMillis >= sendInterval) {
        previousSendMillis = currentMillis;
        float envelopeMV = (filteredEnvelope / 4095.0) * 3300.0;
        client.print("E");
        client.print(DEVICE_ID);
        client.print(":");
        client.println(envelopeMV); 
      }
    }
  }
}

float Filter_lowpass(float inputVal) {
  for (int i = 3; i > 1; i--) { x[i] = x[i - 1]; y[i] = y[i - 1]; }
  x[1] = inputVal;
  y[1] = -a[2]*y[2] - a[3]*y[3] + b[1]*x[1] + b[2]*x[2] + b[3]*x[3];
  return y[1];
}

float Filter_BSF(float inputVal) {
  for (int i = 4; i > 0; i--) { x_bsf[i] = x_bsf[i - 1]; y_bsf[i] = y_bsf[i - 1]; }
  x_bsf[0] = inputVal;
  y_bsf[0] = b_bsf[0]*x_bsf[0] + b_bsf[1]*x_bsf[1] + b_bsf[2]*x_bsf[2] + b_bsf[3]*x_bsf[3] + b_bsf[4]*x_bsf[4]
             - a_bsf[1]*y_bsf[1] - a_bsf[2]*y_bsf[2] - a_bsf[3]*y_bsf[3] - a_bsf[4]*y_bsf[4];
  return y_bsf[0];
}

float Filter_HPF(float inputVal) {
  for (int i = 2; i > 0; i--) { x_hpf[i] = x_hpf[i - 1]; y_hpf[i] = y_hpf[i - 1]; }
  x_hpf[0] = inputVal;
  y_hpf[0] = b_hpf[0]*x_hpf[0] + b_hpf[1]*x_hpf[1] + b_hpf[2]*x_hpf[2] - a_hpf[1]*y_hpf[1] - a_hpf[2]*y_hpf[2];
  return y_hpf[0];
}

void setLED(int r, int g, int b) {
  analogWrite(ledPin1, r); analogWrite(ledPin2, g); analogWrite(ledPin3, b);
}

void updateMosfet(bool state) {
  if (state) { pinMode(PMOSPin, OUTPUT); digitalWrite(PMOSPin, LOW); }
  else { pinMode(PMOSPin, INPUT); }
}

void goToSleep() {
  setLED(0, 0, 0); updateMosfet(false);
  WiFi.disconnect(true); WiFi.mode(WIFI_OFF);
  rtc_gpio_init((gpio_num_t)GPIO_NUM_2);
  rtc_gpio_set_direction((gpio_num_t)GPIO_NUM_2, RTC_GPIO_MODE_INPUT_ONLY);
  rtc_gpio_pulldown_en((gpio_num_t)GPIO_NUM_2);
  esp_sleep_enable_ext1_wakeup(1ULL << GPIO_NUM_2, ESP_EXT1_WAKEUP_ANY_HIGH);
  esp_deep_sleep_start();
}

void tryReconnect() {
  updateMosfet(false); systemActive = false; greenLedDone = false; syncSent = false;
  unsigned long startAttempt = millis();
  while (millis() - startAttempt < wifiTimeoutMs) {
    if (WiFi.status() == WL_CONNECTED) {
      systemActive = true; connectedMillis = millis();
      setLED(cnR, cnG, cnB); 
      delay(2500);
      return; 
    }
    if ((millis() / 1000) % 2 == 0) setLED(srR, srG, srB);
    else setLED(0, 0, 0);
    delay(100);
  }
  goToSleep();
}