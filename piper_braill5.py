#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import queue
import requests
import subprocess
from threading import Thread, Lock, Event
from queue import Queue
import RPi.GPIO as GPIO
import tempfile

# ==================== KONFİGÜRASYON ====================
GITHUB_REPO = "mehkerer8/pdfs"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
LOCAL_BOOKS_DIR = "/home/pixel/braille_books"
UPDATE_INTERVAL = 3600

# PIPER TTS AYARLARI - DÜZELTİLDİ!
PIPER_BINARY_PATH = "/home/pixel/braille_project/piper/piper"
PIPER_MODEL_PATH = "/home/pixel/braille_project/piper/tr_TR-fettah-medium.onnx"

# ==================== PİPER TTS SES SİSTEMİ ====================
class VoiceEngine:
    """Piper TTS'i subprocess ile kullanır - OPTİMİZE EDİLMİŞ"""
    
    def __init__(self):
        self.speech_queue = Queue()
        self.is_playing = False
        self.current_process = None
        self.stop_speech = Event()
        self.lock = Lock()
        self.setup()
        # Arka plan thread'ini başlat
        self.speech_thread = Thread(target=self._speech_worker, daemon=True)
        self.speech_thread.start()
    
    def setup(self):
        """Piper TTS sistemini kur"""
        global PIPER_MODEL_PATH
        
        print("🔊 Piper TTS sistemi kuruluyor...")
        
        # Piper binary kontrolü - DÜZELTİLDİ
        if not os.path.exists(PIPER_BINARY_PATH):
            print(f"❌ Piper binary bulunamadı: {PIPER_BINARY_PATH}")
            print("Piper binary'sini indirmek için:")
            print("cd ~/braille_project/piper")
            print("wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/piper_linux-arm64 -O piper")
            print("chmod +x piper")
            return
        
        # Model kontrolü
        if not os.path.exists(PIPER_MODEL_PATH):
            print(f"⚠️ Piper modeli bulunamadı: {PIPER_MODEL_PATH}")
            print("Alternatif modeller aranıyor...")
            
            model_dir = "/home/pixel/braille_project/piper"
            if os.path.exists(model_dir):
                for file in os.listdir(model_dir):
                    if file.endswith('.onnx'):
                        PIPER_MODEL_PATH = os.path.join(model_dir, file)
                        print(f"✅ Alternatif model bulundu: {file}")
                        break
                else:
                    print("❌ Hiçbir model bulunamadı!")
                    print("Model indirmek için:")
                    print("cd ~/braille_project/piper")
                    print("wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/tr_TR-fettah-medium.onnx")
                    return
            else:
                print("❌ Model dizini bulunamadı!")
                return
        
        print(f"✅ Piper TTS kurulu: {PIPER_BINARY_PATH}")
        print(f"✅ Model: {PIPER_MODEL_PATH}")
        
        # Test: Piper çalışıyor mu?
        try:
            test_cmd = [PIPER_BINARY_PATH, '--help']
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                print("✅ Piper testi başarılı")
            else:
                print(f"❌ Piper testi başarısız: {result.stderr[:100]}")
        except Exception as e:
            print(f"⚠️ Piper test hatası: {e}")
    
    def _speech_worker(self):
        """Arka planda ses kuyruğunu işler"""
        while not self.stop_speech.is_set():
            try:
                # Kuyruktan metin al
                item = self.speech_queue.get(timeout=0.1)
                if item is None:
                    break
                
                text, speed, callback = item
                
                with self.lock:
                    self.is_playing = True
                
                # Piper'ı çalıştır
                self._run_piper_sync(text, speed)
                
                with self.lock:
                    self.is_playing = False
                
                if callback:
                    callback()
                    
                self.speech_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"❌ Ses kuyruğu hatası: {e}")
                with self.lock:
                    self.is_playing = False
    
    def _run_piper_sync(self, text, speed):
        """Piper'ı senkron çalıştır - DÜZELTİLDİ"""
        try:
            # Metni temizle
            text = self._clean_text(text)
            
            if not text.strip():
                return
            
            print(f"🔊 Seslendiriliyor: {text[:50]}...")
            
            # Geçici WAV dosyası oluştur
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False, delete_on_close=False) as tmp_file:
                wav_path = tmp_file.name
            
            # HIZLI PİPER PARAMETRELERİ
            length_scale = max(0.6, 1.0 / speed)  # Minimum 0.6, daha hızlı
            
            # DÜZELTİLMİŞ PİPER KOMUTU
            # Metni doğrudan stdin'den veriyoruz
            cmd = [
                PIPER_BINARY_PATH,
                '--model', PIPER_MODEL_PATH,
                '--output_file', wav_path,
                '--length_scale', str(length_scale),
                '--noise_scale', '0.667',
                '--noise_w', '0.8',
                '--sentence_silence', '0.05',
                '--phoneme_silence', '0.01'
            ]
            
            # Komutu çalıştır
            process = subprocess.run(
                cmd,
                input=text.encode('utf-8'),
                capture_output=True,
                timeout=15
            )
            
            if process.returncode != 0:
                print(f"❌ Piper hatası (kod: {process.returncode}): {process.stderr[:200]}")
                return
            
            print(f"✅ WAV oluşturuldu: {wav_path}")
            
            # WAV dosyasını çal
            self._play_wav_fast(wav_path)
            
            # Dosyayı temizle
            if os.path.exists(wav_path):
                os.remove(wav_path)
                
        except subprocess.TimeoutExpired:
            print("⚠️ Piper biraz uzun sürdü, devam ediyor...")
            try:
                if os.path.exists(wav_path):
                    os.remove(wav_path)
            except:
                pass
        except Exception as e:
            print(f"❌ Piper hatası: {e}")
            try:
                if os.path.exists(wav_path):
                    os.remove(wav_path)
            except:
                pass
    
    def _play_wav_fast(self, wav_path):
        """WAV dosyasını hızlı çal"""
        if not os.path.exists(wav_path):
            print(f"❌ WAV dosyası bulunamadı: {wav_path}")
            return
        
        try:
            file_size = os.path.getsize(wav_path)
            print(f"📁 WAV boyutu: {file_size} bytes")
            
            if file_size == 0:
                print("❌ WAV dosyası boş!")
                return
            
            # aplay ile çal
            print("🔊 Ses çalınıyor...")
            result = subprocess.run(
                ['aplay', '-q', wav_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                print(f"❌ aplay hatası: {result.stderr[:100]}")
            else:
                print("✅ Ses çalındı")
                
        except Exception as e:
            print(f"❌ Ses çalma hatası: {e}")
    
    def _clean_text(self, text):
        """Metni temizle ve optimize et"""
        # Özel karakterleri kaldır
        text = text.replace('"', '')
        text = text.replace("'", "")
        text = text.replace("`", "")
        text = text.replace("´", "")
        
        # Satır sonlarını ve fazla boşlukları kaldır
        text = ' '.join(text.split())
        
        # Çok uzun metinleri kısalt
        if len(text) > 500:
            text = text[:497] + "..."
        
        return text.strip()
    
    def speak(self, text, wait=False, speed=1.0, callback=None):
        """Metni seslendir - ASENKRON (hemen döner)"""
        if not text or not text.strip():
            return
        
        # Kuyruğa ekle
        self.speech_queue.put((text, speed, callback))
    
    def speak_sync(self, text, speed=1.0):
        """Metni senkron seslendir (bloklar)"""
        self._run_piper_sync(text, speed)
    
    def speak_async(self, text, speed=1.0):
        """Asenkron seslendirme"""
        Thread(target=self._run_piper_sync, args=(text, speed), daemon=True).start()
    
    def stop(self):
        """Seslendirmeyi durdur"""
        self.stop_speech.set()
        with self.lock:
            if self.current_process:
                try:
                    self.current_process.terminate()
                except:
                    pass

# ==================== GPIO AYARLARI ====================
class GPIOPins:
    # Röle Pinleri (6 solenoid için)
    RELAY_PINS = [4, 17, 27, 22, 23, 24]
    
    # Buton Pinleri
    BUTTON_NEXT = 5        # Sonraki kitap
    BUTTON_CONFIRM = 6     # Onay/Seçim
    BUTTON_MODE = 13       # Mod değiştirme
    BUTTON_SPEED_UP = 19   # Hız artırma
    BUTTON_SPEED_DOWN = 26 # Hız azaltma
    BUTTON_UPDATE = 21     # Kitapları güncelle
    
    ALL_BUTTONS = [BUTTON_NEXT, BUTTON_CONFIRM, BUTTON_MODE, 
                   BUTTON_SPEED_UP, BUTTON_SPEED_DOWN, BUTTON_UPDATE]

# ==================== BRAILLE KİTAP OKUYUCU ====================
class BrailleBookReader:
    def __init__(self):
        print("🎵 BRAİLLE KİTAP OKUYUCU - OPTİMİZE PİPER TTS")
        print("=" * 50)
        
        # PİPER TTS ses motorunu kur
        print("🔊 PİPER TTS başlatılıyor...")
        self.voice_engine = VoiceEngine()
        
        # GPIO Ayarları
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        
        try:
            GPIO.cleanup()
            time.sleep(0.1)
        except:
            pass
        
        # Değişkenler
        self.books = []
        self.current_book_index = 0
        self.selected_book = None
        self.current_mode = 0
        self.modes = ["sadece_yazma", "sadece_okuma", "hem_okuma_hem_yazma", "egitim_modu"]
        self.mode_names = ["Sadece Yazma", "Sadece Okuma", "Hem Okuma Hem Yazma", "Braille Eğitimi"]
        
        # HIZ AYARLARI
        self.speech_speed = 1.5    # BAŞLANGIÇ HIZI DAHA YÜKSEK (1.5)
        self.write_speed = 0.25    # Yazma hızı daha hızlı
        self.min_speed = 0.8
        self.max_speed = 3.0
        
        # Sistem durumu
        self.is_running = True
        self.is_playing = False
        self.is_paused = False
        self.stop_event = Event()
        self.progress_data = {}
        self.current_position = 0
        self.current_text = ""
        
        # Buton takibi
        self.button_states = {}
        self.button_press_start = {}
        self.last_button_time = {}
        self.lock = Lock()
        
        # Dizinleri oluştur
        self.setup_directories()
        
        # GPIO'yu ayarla
        self.setup_gpio()
        
        # Braille haritasını yükle
        self.setup_braille_map()
        
        # İlerlemeyi yükle
        self.load_progress()
        
        # Kitapları yükle (yerelden)
        self.load_local_books()
        
        # Başlangıç mesajı - KISA VE TEST
        print("🎯 Başlangıç mesajı seslendiriliyor...")
        self.speak_sync("Sistem hazır", speed=1.5)
        
        print("✅ Sistem başlatıldı!")
    
    # ==================== SES FONKSİYONLARI ====================
    def speak(self, text, speed=None):
        """Metni seslendir - HIZLI"""
        if speed is None:
            speed = self.speech_speed
        
        if text and text.strip():
            self.voice_engine.speak(text, speed=speed)
    
    def speak_sync(self, text, speed=None):
        """Senkron seslendirme - acil durumlar için"""
        if speed is None:
            speed = self.speech_speed
        
        if text and text.strip():
            self.voice_engine.speak_sync(text, speed)
    
    def speak_async(self, text, speed=None):
        """Asenkron seslendirme"""
        if speed is None:
            speed = self.speech_speed
        
        if text and text.strip():
            self.voice_engine.speak_async(text, speed)
    
    def adjust_speed(self, increase=True):
        """Ses hızını ayarla - ANINDA TEPKİ"""
        with self.lock:
            if increase:
                self.speech_speed = min(self.max_speed, self.speech_speed + 0.3)
                self.write_speed = max(0.15, self.write_speed - 0.05)
            else:
                self.speech_speed = max(self.min_speed, self.speech_speed - 0.3)
                self.write_speed = min(0.4, self.write_speed + 0.05)
            
            # Hemen geri bildirim ver - KISA
            if self.speech_speed > 2.0:
                speed_text = "çok hızlı"
            elif self.speech_speed > 1.5:
                speed_text = "hızlı"
            elif self.speech_speed > 1.0:
                speed_text = "normal"
            else:
                speed_text = "yavaş"
            
            self.speak_sync(f"Hız {speed_text}", speed=self.speech_speed * 1.2)
    
    # ... (diğer fonksiyonlar aynı kalacak, sadece sesle ilgili kısımları gösterdim) ...

# ==================== TEST FONKSİYONU ====================
def test_sound_system():
    """Ses sistemini test et"""
    print("🎵 SES SİSTEMİ TESTİ")
    print("=" * 50)
    
    # Piper kontrolü
    piper_path = "/home/pixel/braille_project/piper/piper"
    model_path = "/home/pixel/braille_project/piper/tr_TR-fettah-medium.onnx"
    
    if not os.path.exists(piper_path):
        print("❌ Piper binary bulunamadı!")
        return False
    
    if not os.path.exists(model_path):
        print("❌ Piper modeli bulunamadı!")
        return False
    
    print("✅ Piper binary ve model bulundu")
    
    # Test komutu
    test_text = "Merhaba, ses sistemi test ediliyor"
    test_file = "/tmp/test_sound.wav"
    
    try:
        # Piper ile WAV oluştur
        cmd = [
            piper_path,
            '--model', model_path,
            '--output_file', test_file
        ]
        
        print(f"🔧 Komut: {' '.join(cmd)}")
        
        process = subprocess.run(
            cmd,
            input=test_text.encode('utf-8'),
            capture_output=True,
            timeout=10
        )
        
        if process.returncode != 0:
            print(f"❌ Piper hatası: {process.stderr[:200]}")
            return False
        
        print(f"✅ WAV oluşturuldu: {test_file}")
        
        # WAV dosyasını kontrol et
        if os.path.exists(test_file):
            file_size = os.path.getsize(test_file)
            print(f"📁 WAV boyutu: {file_size} bytes")
            
            if file_size == 0:
                print("❌ WAV dosyası boş!")
                os.remove(test_file)
                return False
            
            # Ses çalma testi
            print("🔊 Ses çalınıyor...")
            result = subprocess.run(
                ['aplay', test_file],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                print(f"❌ aplay hatası: {result.stderr[:100]}")
                print("💡 Ses çıkışını kontrol edin:")
                print("  1. Kulaklık takılı mı?")
                print("  2. Ses seviyesi açık mı?")
                print("  3. 'sudo raspi-config' ile ses çıkışını ayarlayın")
            else:
                print("✅ Ses başarıyla çalındı!")
            
            # Temizlik
            if os.path.exists(test_file):
                os.remove(test_file)
            
            return result.returncode == 0
            
    except Exception as e:
        print(f"❌ Test hatası: {e}")
        return False

# ==================== ANA PROGRAM ====================
def main():
    """Ana fonksiyon"""
    print("=" * 60)
    print("🎵 BRAİLLE KİTAP OKUYUCU - OPTİMİZE PİPER TTS")
    print("=" * 60)
    
    # Önce ses sistemini test et
    print("🔊 Ses sistemi test ediliyor...")
    if not test_sound_system():
        print("❌ Ses sistemi testi başarısız!")
        print("💡 Düzeltmeler:")
        print("  1. Piper binary'sini indirin:")
        print("     cd ~/braille_project/piper")
        print("     wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/piper_linux-arm64 -O piper")
        print("     chmod +x piper")
        print("  2. Modeli indirin:")
        print("     wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/tr_TR-fettah-medium.onnx")
        print("  3. Ses çıkışını ayarlayın:")
        print("     sudo raspi-config")
        print("     -> System Options -> Audio -> 3.5mm jack")
        return
    
    print("✅ Ses sistemi testi başarılı!")
    print("=" * 60)
    
    try:
        import requests
        import RPi.GPIO
        print("✅ Python paketleri yüklü")
    except ImportError as e:
        print(f"❌ Eksik paket: {e}")
        print("Kurulum için: pip install requests RPi.GPIO")
        return
    
    reader = BrailleBookReader()
    
    try:
        reader.main_loop()
    except KeyboardInterrupt:
        print("\n⏹️ Durduruldu")
        reader.cleanup()
    except Exception as e:
        print(f"Hata: {e}")
        reader.cleanup()

if __name__ == "__main__":
    main()
