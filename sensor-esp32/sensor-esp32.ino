// =====================================================================
//  Sensor de presença por Wi-Fi (CSI) para o Painel de Anúncios
// =====================================================================
//  A placa ESP32 fica conectada ao seu roteador e "escuta" como o sinal
//  Wi-Fi chega até ela. Quando alguém se mexe entre a placa e o roteador,
//  o sinal oscila (o corpo humano reflete e absorve as ondas). O programa
//  mede essa oscilação e avisa o painel: movimento / parado.
//
//  Placas que funcionam: ESP32 (DevKit comum), ESP32-S3, ESP32-C3.
//  Arduino IDE: instale "esp32 by Espressif Systems" versão 3.x no
//  Gerenciador de Placas. Instruções completas: sensor-esp32/LEIAME.md
// =====================================================================
#include <WiFi.h>
#include <HTTPClient.h>
#include "esp_wifi.h"
#include "ping/ping_sock.h"
#include "lwip/ip_addr.h"

// ------------------- CONFIGURE AQUI ----------------------------------
const char* WIFI_NOME    = "NOME_DO_WIFI";
const char* WIFI_SENHA   = "SENHA_DO_WIFI";
const char* PAINEL_IP    = "192.168.0.10";   // aparece no painel, em "Como instalar um sensor"
const int   PAINEL_PORTA = 8080;
const char* TOKEN        = "COLE_O_CODIGO";  // aparece no painel, em "Como instalar um sensor"
const char* SENSOR_ID    = "esp32-entrada";  // um nome diferente para cada placa
const char* SENSOR_NOME  = "Entrada da loja";

// Sensibilidade: número MENOR = mais sensível. Se disparar sozinho com a
// sala vazia, aumente (ex.: 6). Se não perceber as pessoas, diminua (ex.: 2.5).
const float SENSIBILIDADE = 4.0;
// ---------------------------------------------------------------------

const int   PACOTES_SEG   = 50;     // pings por segundo ao roteador (gera o sinal medido)
const int   JANELA        = 50;     // ~1 s de amostras para medir a oscilação
const int   CALIBRA_SEG   = 15;     // calibração com o ambiente vazio ao ligar
const int   PARADO_MS     = 4000;   // sem oscilação por 4 s = parado
const int   HEARTBEAT_MS  = 30000;  // avisa o painel pelo menos a cada 30 s

static QueueHandle_t fila;          // "turbulência" de cada pacote recebido
static uint8_t bssid[6];

// Chamado pelo Wi-Fi a cada pacote recebido com informação de canal (CSI).
// Calcula a amplitude de cada subportadora e guarda o desvio-padrão entre elas.
static void aoReceberCsi(void* ctx, wifi_csi_info_t* info) {
  if (!info || !info->buf || memcmp(info->mac, bssid, 6) != 0) return; // só pacotes do roteador
  const int8_t* b = info->buf;
  int n = info->len / 2;
  if (n > 64) n = 64;                 // LLTF: 64 subportadoras
  float soma = 0, soma2 = 0; int cont = 0;
  for (int i = 0; i < n; i++) {
    float im = b[2 * i], re = b[2 * i + 1];
    float a = sqrtf(re * re + im * im);
    if (a < 1) continue;              // subportadoras nulas / de guarda
    soma += a; soma2 += a * a; cont++;
  }
  if (cont < 10) return;
  float media = soma / cont;
  float turb = sqrtf(fmaxf(soma2 / cont - media * media, 0));
  xQueueSend(fila, &turb, 0);
}

static void iniciarCsi() {
  memcpy(bssid, WiFi.BSSID(), 6);
  wifi_csi_config_t c = {};
  c.lltf_en = true;
  c.htltf_en = false;
  c.stbc_htltf2_en = false;
  c.ltf_merge_en = true;
  c.channel_filter_en = false;
  c.manu_scale = false;
  ESP_ERROR_CHECK(esp_wifi_set_csi_config(&c));
  ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(aoReceberCsi, nullptr));
  ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

// Pinga o roteador sem parar: cada resposta é um pacote com CSI para medir.
static void iniciarPing() {
  esp_ping_config_t cfg = ESP_PING_DEFAULT_CONFIG();
  ipaddr_aton(WiFi.gatewayIP().toString().c_str(), &cfg.target_addr);
  cfg.count = ESP_PING_COUNT_INFINITE;
  cfg.interval_ms = 1000 / PACOTES_SEG;
  cfg.timeout_ms = 500;
  cfg.data_size = 1;
  esp_ping_callbacks_t cbs = {};
  esp_ping_handle_t h;
  esp_ping_new_session(&cfg, &cbs, &h);
  esp_ping_start(h);
}

// Variância móvel da turbulência: parado ≈ constante, movimento = oscila.
static float jan[JANELA];
static int jIdx = 0, jCont = 0;
static float adicionar(float t) {
  jan[jIdx] = t; jIdx = (jIdx + 1) % JANELA; if (jCont < JANELA) jCont++;
  float m = 0; for (int i = 0; i < jCont; i++) m += jan[i]; m /= jCont;
  float v = 0; for (int i = 0; i < jCont; i++) v += (jan[i] - m) * (jan[i] - m);
  return v / jCont;
}

static bool avisarPainel(bool movimento, float intensidade) {
  if (WiFi.status() != WL_CONNECTED) return false;
  HTTPClient http;
  String url = String("http://") + PAINEL_IP + ":" + PAINEL_PORTA + "/api/presenca/evento";
  http.begin(url);
  http.setTimeout(3000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("x-token", TOKEN);
  String corpo = String("{\"sensor\":\"") + SENSOR_ID + "\",\"nome\":\"" + SENSOR_NOME +
                 "\",\"origem\":\"esp32\",\"movimento\":" + (movimento ? "true" : "false") +
                 ",\"intensidade\":" + String(intensidade, 2) + "}";
  int r = http.POST(corpo);
  http.end();
  if (r != 200) Serial.printf("Painel respondeu %d (confira IP, porta e TOKEN)\n", r);
  return r == 200;
}

static void conectarWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);               // sem economia de energia: CSI contínuo
  WiFi.begin(WIFI_NOME, WIFI_SENHA);
  Serial.print("Conectando ao Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.printf("\nConectado. IP %s, roteador %s, sinal %d dBm\n",
                WiFi.localIP().toString().c_str(), WiFi.gatewayIP().toString().c_str(), WiFi.RSSI());
}

float limiar = 0;
bool movimento = false;                  // último estado avisado ao painel
bool falhou = false;                     // o último aviso ao painel falhou?
unsigned long ultimoMov = 0, ultimoAviso = 0, ultimaTentativa = 0, ultimoLog = 0, inicioSeg = 0;
float picoSeg = 0, picoAnterior = 0;     // maior oscilação no segundo atual / no anterior

void setup() {
  Serial.begin(115200);
  fila = xQueueCreate(256, sizeof(float));
  conectarWifi();
  iniciarCsi();
  iniciarPing();

  // Calibração: mede o "ruído" normal do ambiente vazio.
  Serial.printf("Calibrando por %d s. Deixe o ambiente VAZIO e parado...\n", CALIBRA_SEG);
  float soma = 0, soma2 = 0; int n = 0; float t;
  unsigned long fim = millis() + CALIBRA_SEG * 1000UL;
  while (millis() < fim) {
    if (xQueueReceive(fila, &t, pdMS_TO_TICKS(100)) == pdTRUE) {
      float v = adicionar(t);
      if (jCont == JANELA) { soma += v; soma2 += v * v; n++; }
    }
  }
  if (n < 50) {
    Serial.println("Poucos pacotes recebidos. Confira se a placa é ESP32/S3/C3 e se o roteador responde ping. Reiniciando...");
    delay(3000); ESP.restart();
  }
  float media = soma / n, desvio = sqrtf(fmaxf(soma2 / n - media * media, 0));
  limiar = fmaxf(media + SENSIBILIDADE * desvio, media * 2 + 0.05f);
  Serial.printf("Pronto. Ruído médio %.3f, limiar %.3f (%d pacotes)\n", media, limiar, n);
  avisarPainel(false, 0);
  ultimoAviso = millis();
}

void loop() {
  float t;
  while (xQueueReceive(fila, &t, pdMS_TO_TICKS(20)) == pdTRUE) {
    float v = adicionar(t);
    if (v > picoSeg) picoSeg = v;
    if (v > limiar) ultimoMov = millis();
  }
  unsigned long agora = millis();
  if (agora - inicioSeg >= 1000) { picoAnterior = picoSeg; picoSeg = 0; inicioSeg = agora; }
  bool mov = ultimoMov != 0 && agora - ultimoMov < PARADO_MS;
  float intensidade = fmaxf(picoSeg, picoAnterior) / limiar;  // acima de 1 = movimento

  // Abra o "Plotter Serial" da Arduino IDE para ver o gráfico e ajustar a sensibilidade.
  if (agora - ultimoLog >= 250) {
    Serial.printf("intensidade:%.2f limite:1 movimento:%d\n", intensidade, mov ? 2 : 0);
    ultimoLog = agora;
  }

  // Avisa quando muda o estado e, de qualquer jeito, a cada 30 s. Se o painel
  // não responder, tenta de novo a cada 5 s.
  bool precisa = mov != movimento || agora - ultimoAviso > HEARTBEAT_MS;
  if (precisa && (!falhou || agora - ultimaTentativa > 5000)) {
    ultimaTentativa = agora;
    falhou = !avisarPainel(mov, intensidade);
    if (!falhou) { movimento = mov; ultimoAviso = agora; }
  }

  if (WiFi.status() != WL_CONNECTED) { Serial.println("Wi-Fi caiu, reiniciando..."); delay(1000); ESP.restart(); }
}
