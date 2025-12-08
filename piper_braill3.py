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
PIPER_BINARY_PATH = "/home/pixel/braille_project/piper/piper"  # TAM YOL
PIPER_MODEL_PATH = "/home/pixel/braille_project/piper/tr_TR-fettah-medium.onnx"  # TAM YOL

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
        global PIPER_MODEL_PATH  # Global değişkeni değiştirmek için
        
        print("🔊 Piper TTS sistemi kuruluyor...")
        
        # Piper binary kontrolü
        if not os.path.exists(PIPER_BINARY_PATH):
            print("❌ Piper binary bulunamadı!")
            print("Lütfen şu komutları çalıştırın:")
            print("  cd ~/braille_project/piper")
            print("  wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/piper_linux-arm64")
            print("  mv piper_linux-arm64 piper")
            print("  chmod +x piper")
            print("  wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/tr_TR-fettah-medium.onnx")
            return
        
        # Model kontrolü
        if not os.path.exists(PIPER_MODEL_PATH):
            print("⚠️ Piper modeli bulunamadı!")
            print("Lütfen şu komutla indirin:")
            print("  cd ~/braille_project/piper")
            print("  wget https://github.com/rhasspy/piper/releases/download/2023.12.06-09.23.38/tr_TR-fettah-medium.onnx")
            # Geçici olarak başka bir model kullan
            model_dir = "/home/pixel/braille_project/piper"
            for file in os.listdir(model_dir):
                if file.endswith('.onnx'):
                    # Alternatif model bulundu, global değişkeni güncelle
                    PIPER_MODEL_PATH = os.path.join(model_dir, file)
                    print(f"✅ Alternatif model bulundu: {file}")
                    break
        
        print(f"✅ Piper TTS kurulu: {PIPER_BINARY_PATH}")
        print(f"✅ Model: {PIPER_MODEL_PATH}")
    
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
        """Piper'ı senkron çalıştır - OPTİMİZE"""
        try:
            # Metni temizle ve kısalt
            text = self._clean_text(text)
            
            if not text.strip():
                return
            
            # Geçici WAV dosyası oluştur
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
                wav_path = tmp_file.name
            
            # HIZLI PİPER PARAMETRELERİ
            length_scale = max(0.6, 1.0 / speed)  # Minimum 0.6, daha hızlı
            
            # OPTİMİZE PİPER KOMUTU
            cmd = [
                'echo', f'"{text}"', '|',
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
                ' '.join(cmd),
                shell=True,
                capture_output=True,
                text=True,
                timeout=15
            )
            
            if process.returncode != 0:
                print(f"❌ Piper hatası: {process.stderr[:100]}")
                return
            
            # WAV dosyasını çal
            self._play_wav_fast(wav_path)
            
            # Dosyayı temizle
            if os.path.exists(wav_path):
                os.remove(wav_path)
                
        except subprocess.TimeoutExpired:
            print("⚠️ Piper biraz uzun sürdü, devam ediyor...")
        except Exception as e:
            print(f"❌ Piper hatası: {e}")
    
    def _play_wav_fast(self, wav_path):
        """WAV dosyasını hızlı çal"""
        if not os.path.exists(wav_path):
            return
        
        try:
            # aplay ile çal
            subprocess.run(
                ['aplay', '-q', '--buffer-time=50000', wav_path],
                capture_output=True,
                timeout=10
            )
        except Exception as e:
            print(f"❌ Ses çalma hatası: {e}")
    
    def _clean_text(self, text):
        """Metni temizle ve optimize et"""
        # Tırnak işaretlerini escape et
        text = text.replace('"', '\\"')
        # Satır sonlarını ve fazla boşlukları kaldır
        text = ' '.join(text.split())
        # Çok uzun metinleri kısalt
        if len(text) > 500:
            text = text[:497] + "..."
        return text
    
    def speak(self, text, wait=False, speed=1.0, callback=None):
        """Metni seslendir - ASENKRON (hemen döner)"""
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
        
        # Başlangıç mesajı - TEK SEFERDE, HIZLI
        welcome_parts = []
        welcome_parts.append("Braille kitap okuyucuya hoş geldiniz")
        
        if self.books:
            welcome_parts.append(f"Kütüphanede {len(self.books)} kitap var")
            welcome_parts.append(f"İlk kitap: {self.books[0]['name_tr']}")
        else:
            welcome_parts.append("Henüz kitap yok")
            welcome_parts.append("Güncelle tuşu ile kitapları indirin")
        
        welcome_parts.append("İleri tuşu: kitaplar arasında gezin")
        welcome_parts.append("Onay tuşu: seç veya duraklat")
        welcome_parts.append("Mod tuşu: okuma modunu değiştir")
        welcome_parts.append("Hız tuşları: okuma hızını ayarla")
        
        # TÜM MENÜYÜ TEK CÜMLEDE SÖYLE - HIZLI
        welcome_text = ". ".join(welcome_parts)
        self.speak(welcome_text, speed=1.8)  # DAHA HIZLI
        
        print("✅ Sistem başlatıldı!")
    
    # ==================== SES FONKSİYONLARI ====================
    def speak(self, text, speed=None):
        """Metni seslendir - HIZLI"""
        if speed is None:
            speed = self.speech_speed
        self.voice_engine.speak(text, speed=speed)
    
    def speak_sync(self, text, speed=None):
        """Senkron seslendirme - acil durumlar için"""
        if speed is None:
            speed = self.speech_speed
        self.voice_engine.speak_sync(text, speed)
    
    def speak_async(self, text, speed=None):
        """Asenkron seslendirme"""
        if speed is None:
            speed = self.speech_speed
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
            
            self.speak(f"Hız {speed_text}", speed=self.speech_speed * 1.2)
    
    # ==================== GİTHUB PDF SİSTEMİ ====================
    def setup_directories(self):
        """Gerekli dizinleri oluştur"""
        os.makedirs(LOCAL_BOOKS_DIR, exist_ok=True)
        os.makedirs(f"{LOCAL_BOOKS_DIR}/pdfs", exist_ok=True)
    
    def load_local_books(self):
        """Yerel kitapları yükle"""
        auto_file = f"{LOCAL_BOOKS_DIR}/kitaplar_auto.json"
        
        if os.path.exists(auto_file):
            try:
                with open(auto_file, 'r', encoding='utf-8') as f:
                    self.books = json.load(f)
                print(f"📚 {len(self.books)} kitap yüklendi")
            except Exception as e:
                print(f"Kitaplar yüklenirken hata: {e}")
                self.books = []
        else:
            self.books = []
    
    def scan_github_for_pdfs(self):
        """GitHub'daki PDF'leri tara"""
        print("🌐 GitHub'daki PDF'ler taranıyor...")
        
        try:
            headers = {'User-Agent': 'Braille-Book-Reader'}
            response = requests.get(GITHUB_API_URL, headers=headers, timeout=10)
            
            if response.status_code == 200:
                files = response.json()
                books = []
                
                for file in files:
                    if isinstance(file, dict) and file.get('type') == 'file':
                        filename = file.get('name', '')
                        if filename.lower().endswith('.pdf'):
                            book_name = self.create_book_name(filename)
                            books.append({
                                'filename': filename,
                                'name_tr': book_name,
                                'download_url': file.get('download_url', ''),
                                'size': file.get('size', 0),
                                'sha': file.get('sha', '')[:8]
                            })
                
                print(f"✅ {len(books)} PDF bulundu")
                return books
            else:
                print(f"❌ GitHub API hatası: {response.status_code}")
                return []
                
        except Exception as e:
            print(f"❌ Tarama hatası: {e}")
            return []
    
    def create_book_name(self, filename):
        """Dosya adından kitap adı oluştur"""
        name = filename.replace('.pdf', '').replace('.PDF', '')
        for char in ['_', '-', '.']:
            name = name.replace(char, ' ')
        
        words = []
        for word in name.split():
            if word.lower() in ['ve', 'ile', 'de', 'da', 'ki']:
                words.append(word.lower())
            else:
                words.append(word[0].upper() + word[1:].lower())
        
        result = ' '.join(words)
        return result[:40] if len(result) > 40 else result
    
    def update_library(self, speak_progress=True):
        """Kitaplığı güncelle"""
        if speak_progress:
            self.speak("Kitaplar güncelleniyor", speed=1.8)
        
        github_books = self.scan_github_for_pdfs()
        
        if not github_books:
            if speak_progress:
                self.speak("Kitap listesi alınamadı")
            return
        
        if speak_progress:
            self.speak(f"{len(github_books)} kitap bulundu", speed=1.8)
        
        new_books = []
        for book in github_books:
            local_path = f"{LOCAL_BOOKS_DIR}/pdfs/{book['filename']}"
            if not os.path.exists(local_path):
                new_books.append(book)
        
        if speak_progress and new_books:
            self.speak(f"{len(new_books)} yeni kitap indirilecek", speed=1.8)
        
        success_count = 0
        for book in new_books:
            if self.download_book(book):
                success_count += 1
        
        self.save_book_metadata(github_books)
        self.books = github_books
        
        if speak_progress:
            if success_count > 0:
                self.speak(f"{success_count} kitap eklendi", speed=1.8)
            else:
                self.speak("Tüm kitaplar güncel", speed=1.8)
    
    def download_book(self, book):
        """Kitabı indir"""
        try:
            response = requests.get(book['download_url'], timeout=30)
            if response.status_code == 200:
                file_path = f"{LOCAL_BOOKS_DIR}/pdfs/{book['filename']}"
                with open(file_path, 'wb') as f:
                    f.write(response.content)
                print(f"📥 {book['filename']} indirildi")
                return True
            else:
                print(f"❌ {book['filename']} indirilemedi: {response.status_code}")
        except Exception as e:
            print(f"❌ {book['filename']} indirme hatası: {e}")
        return False
    
    def save_book_metadata(self, books):
        """Metadata'yı kaydet"""
        metadata_path = f"{LOCAL_BOOKS_DIR}/kitaplar_auto.json"
        try:
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(books, f, ensure_ascii=False, indent=2)
            print("📁 Metadata kaydedildi")
        except Exception as e:
            print(f"❌ Metadata kaydetme hatası: {e}")
    
    # ==================== GPIO ve BUTON KONTROLÜ ====================
    def setup_gpio(self):
        """GPIO pinlerini ayarla"""
        try:
            # Röle pinleri
            for pin in GPIOPins.RELAY_PINS:
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.LOW)
            
            # Buton pinleri
            for pin in GPIOPins.ALL_BUTTONS:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                self.button_states[pin] = GPIO.HIGH
                self.button_press_start[pin] = 0
                self.last_button_time[pin] = time.time()
            
            print("✅ GPIO ayarlandı")
            
        except Exception as e:
            print(f"❌ GPIO hatası: {e}")
    
    def check_buttons(self):
        """Butonları kontrol et - HIZLI"""
        current_time = time.time()
        
        for pin in GPIOPins.ALL_BUTTONS:
            try:
                current_state = GPIO.input(pin)
                last_state = self.button_states.get(pin, GPIO.HIGH)
                
                # Buton basıldı
                if current_state == GPIO.LOW and last_state == GPIO.HIGH:
                    self.button_press_start[pin] = current_time
                    self.last_button_time[pin] = current_time
                    self.handle_button_press(pin)
                
                # Buton basılı tutuluyor
                elif current_state == GPIO.LOW and last_state == GPIO.LOW:
                    press_duration = current_time - self.button_press_start[pin]
                    
                    # 1.5 saniye basılı tutunca BAŞTAN BAŞLAT
                    if press_duration >= 1.5 and pin == GPIOPins.BUTTON_NEXT:
                        if self.is_playing and not self.is_paused:
                            self.handle_long_press(pin, press_duration)
                            self.button_press_start[pin] = current_time
                
                # Buton bırakıldı
                elif current_state == GPIO.HIGH and last_state == GPIO.LOW:
                    self.button_press_start[pin] = 0
                
                self.button_states[pin] = current_state
                
            except Exception as e:
                pass  # Hataları görmezden gel, hız için
    
    def handle_button_press(self, pin):
        """Kısa basma işleyici - ANINDA TEPKİ"""
        # DEBOUNCE: Aynı butona çok hızlı basmaları engelle
        current_time = time.time()
        if current_time - self.last_button_time.get(pin, 0) < 0.3:  # 300ms debounce
            return
        
        with self.lock:
            if pin == GPIOPins.BUTTON_NEXT:
                Thread(target=self.next_book, daemon=True).start()
            elif pin == GPIOPins.BUTTON_CONFIRM:
                Thread(target=self.confirm_selection, daemon=True).start()
            elif pin == GPIOPins.BUTTON_MODE:
                Thread(target=self.next_mode, daemon=True).start()
            elif pin == GPIOPins.BUTTON_SPEED_UP:
                Thread(target=self.adjust_speed, args=(True,), daemon=True).start()
            elif pin == GPIOPins.BUTTON_SPEED_DOWN:
                Thread(target=self.adjust_speed, args=(False,), daemon=True).start()
            elif pin == GPIOPins.BUTTON_UPDATE:
                Thread(target=self.manual_update, daemon=True).start()
    
    def handle_long_press(self, pin, duration):
        """Uzun basma işleyici"""
        if pin == GPIOPins.BUTTON_NEXT and self.is_playing and not self.is_paused:
            print(f"⏪ Kitap baştan başlatılıyor...")
            self.speak("Baştan", speed=2.0)
            
            self.stop_event.set()
            time.sleep(0.1)
            self.stop_event.clear()
            
            self.current_position = 0
            
            if self.selected_book:
                book_key = self.selected_book['filename']
                self.progress_data[book_key] = {
                    'position': 0,
                    'mode': self.current_mode,
                    'timestamp': time.time()
                }
                self.save_progress()
            
            self.start_reading()
    
    def next_book(self):
        """Sonraki kitap - HIZLI"""
        if not self.books:
            self.speak("Kitap yok", speed=2.0)
            return
        
        self.current_book_index = (self.current_book_index + 1) % len(self.books)
        book = self.books[self.current_book_index]
        self.speak(book['name_tr'], speed=1.8)
    
    def confirm_selection(self):
        """Seçimi onayla veya DURAKLAT/DEVAM ET - HIZLI"""
        if not self.books:
            self.speak("Önce güncelle", speed=2.0)
            return
        
        if self.selected_book is None:
            self.selected_book = self.books[self.current_book_index]
            book = self.selected_book
            self.speak(f"{book['name_tr']} seçildi", speed=1.8)
        elif self.is_playing:
            self.toggle_pause()
        else:
            self.speak(f"{self.mode_names[self.current_mode]} başlıyor", speed=1.8)
            time.sleep(0.2)
            self.start_reading()
    
    def toggle_pause(self):
        """Duraklat/Devam et - HIZLI"""
        if not self.is_playing:
            return
        
        self.is_paused = not self.is_paused
        
        if self.is_paused:
            self.speak("Duraklatıldı", speed=2.0)
            self.clear_solenoids()
        else:
            self.speak("Devam", speed=2.0)
    
    def next_mode(self):
        """Sonraki mod - HIZLI"""
        if self.selected_book is None:
            self.speak("Önce kitap seç", speed=2.0)
            return
        
        self.current_mode = (self.current_mode + 1) % len(self.modes)
        self.speak(self.mode_names[self.current_mode], speed=1.8)
    
    def manual_update(self):
        """Manuel güncelleme"""
        Thread(target=self.update_library, args=(True,), daemon=True).start()
    
    # ==================== BRAILLE SİSTEMİ ====================
    def setup_braille_map(self):
        """Braille haritasını yükle"""
        self.braille_map = {
            'a': [1,0,0,0,0,0], 'b': [1,1,0,0,0,0], 'c': [1,0,0,1,0,0],
            'ç': [1,0,0,1,1,0], 'd': [1,0,0,1,1,1], 'e': [1,0,0,0,1,0],
            'f': [1,1,0,1,0,0], 'g': [1,1,0,1,1,0], 'ğ': [1,1,0,1,1,1],
            'h': [1,1,0,0,1,0], 'ı': [0,1,0,1,0,1], 'i': [0,1,0,1,0,0],
            'j': [0,1,0,1,1,0], 'k': [1,0,1,0,0,0], 'l': [1,1,1,0,0,0],
            'm': [1,0,1,1,0,0], 'n': [1,0,1,1,1,0], 'o': [1,0,1,0,1,0],
            'ö': [0,1,1,1,0,1], 'p': [1,1,1,1,0,0], 'r': [1,1,1,1,1,0],
            's': [0,1,1,1,0,0], 'ş': [1,1,1,0,1,1], 't': [0,1,1,1,1,1],
            'u': [1,0,1,0,0,1], 'ü': [0,1,1,1,1,0], 'v': [0,1,1,1,0,1],
            'y': [1,0,1,1,1,1], 'z': [1,0,1,0,1,1],
            ' ': [0,0,0,0,0,0], '.': [0,1,0,0,1,1], ',': [0,1,0,0,0,0],
            '!': [0,1,1,0,1,0], '?': [0,1,1,0,0,1]
        }
    
    def set_solenoids(self, pattern):
        """Solenoidleri ayarla"""
        for i, state in enumerate(pattern[:6]):
            if i < len(GPIOPins.RELAY_PINS):
                GPIO.output(GPIOPins.RELAY_PINS[i], GPIO.HIGH if state else GPIO.LOW)
    
    def clear_solenoids(self):
        """Solenoidleri temizle"""
        for pin in GPIOPins.RELAY_PINS:
            GPIO.output(pin, GPIO.LOW)
    
    def write_character_fast(self, char):
        """Bir karakteri ÇOK HIZLI yaz"""
        char_lower = char.lower()
        if char_lower in self.braille_map:
            pattern = self.braille_map[char_lower]
            self.set_solenoids(pattern)
            time.sleep(self.write_speed)
            self.clear_solenoids()
            time.sleep(0.01)
            return True
        elif char == ' ':
            time.sleep(self.write_speed)
            return True
        return False
    
    def write_word_fast(self, word):
        """Bir kelimeyi ÇOK HIZLI yaz"""
        for char in word:
            if self.stop_event.is_set() or not self.is_playing or self.is_paused:
                return False
            
            while self.is_paused and self.is_playing and not self.stop_event.is_set():
                time.sleep(0.05)  # Daha sık kontrol
            
            if not self.write_character_fast(char):
                return False
        
        return True
    
    # ==================== PDF OKUMA ====================
    def read_pdf_content(self, book):
        """PDF içeriğini oku"""
        pdf_path = f"{LOCAL_BOOKS_DIR}/pdfs/{book['filename']}"
        
        if not os.path.exists(pdf_path):
            return ""
        
        try:
            # pdftotext kontrolü
            if not os.path.exists('/usr/bin/pdftotext'):
                print("⚠️ pdftotext kurulu değil. Kurmak için: sudo apt-get install poppler-utils")
                return "PDF okuma özelliği için pdftotext kurulu değil."
            
            temp_file = "/tmp/kitap_temp.txt"
            cmd = ["pdftotext", "-layout", "-enc", "UTF-8", pdf_path, temp_file]
            subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            
            if os.path.exists(temp_file):
                with open(temp_file, 'r', encoding='utf-8', errors='ignore') as f:
                    text = f.read()
                os.remove(temp_file)
                
                text = ' '.join(text.split())
                return text[:3000]  # DAHA AZ KARAKTER
            return ""
        except Exception as e:
            print(f"PDF okuma hatası: {e}")
            return ""
    
    def start_reading(self):
        """Okumaya başla"""
        if not self.selected_book:
            return
        
        self.stop_event.set()
        self.is_playing = False
        self.is_paused = False
        time.sleep(0.1)
        self.stop_event.clear()
        
        self.speak("Yükleniyor", speed=2.0)
        self.current_text = self.read_pdf_content(self.selected_book)
        
        if not self.current_text or len(self.current_text) < 10:
            self.speak("Kitap boş")
            return
        
        book_key = self.selected_book['filename']
        if book_key in self.progress_data:
            self.current_position = self.progress_data[book_key]['position']
            self.speak("Devam ediliyor", speed=2.0)
        else:
            self.current_position = 0
        
        self.is_playing = True
        
        # Modları ayrı thread'lerde başlat
        if self.modes[self.current_mode] == "sadece_yazma":
            Thread(target=self.mode_write_only, daemon=True).start()
        elif self.modes[self.current_mode] == "sadece_okuma":
            Thread(target=self.mode_read_only, daemon=True).start()
        elif self.modes[self.current_mode] == "hem_okuma_hem_yazma":
            Thread(target=self.mode_read_and_write, daemon=True).start()
        elif self.modes[self.current_mode] == "egitim_modu":
            Thread(target=self.mode_education, daemon=True).start()
    
    def mode_write_only(self):
        """Sadece yazma modu"""
        self.speak("Yazma modu", speed=1.8)
        
        text_to_write = self.current_text[self.current_position:self.current_position + 150]
        
        char_count = 0
        for char in text_to_write:
            if self.stop_event.is_set() or not self.is_playing:
                break
            
            while self.is_paused and self.is_playing and not self.stop_event.is_set():
                time.sleep(0.05)
            
            if self.write_character_fast(char):
                char_count += 1
                self.current_position += 1
            
            if char_count % 20 == 0:
                self.save_progress()
        
        self.is_playing = False
        self.save_progress()
        self.speak("Yazma bitti", speed=1.8)
    
    def mode_read_only(self):
        """Sadece okuma modu"""
        self.speak("Okuma modu", speed=1.8)
        
        text_to_read = self.current_text[self.current_position:self.current_position + 500]
        
        if text_to_read.strip():
            self.speak(text_to_read, speed=self.speech_speed)
        
        self.current_position += len(text_to_read)
        self.save_progress()
        
        self.is_playing = False
        self.speak("Okuma bitti", speed=1.8)
    
    def mode_read_and_write(self):
        """Hem okuma hem yazma modu - OPTİMİZE"""
        self.speak("Okuma yazma modu", speed=1.8)
        
        while self.is_playing and not self.stop_event.is_set():
            while self.is_paused and self.is_playing and not self.stop_event.is_set():
                time.sleep(0.05)
            
            text_chunk = self.current_text[self.current_position:self.current_position + 200]
            
            if not text_chunk.strip():
                self.current_position = 0
                text_chunk = self.current_text[self.current_position:self.current_position + 200]
                
                if not text_chunk.strip():
                    break
            
            words = text_chunk.split()
            
            for word in words:
                if self.stop_event.is_set() or not self.is_playing:
                    break
                
                while self.is_paused and self.is_playing and not self.stop_event.is_set():
                    time.sleep(0.05)
                
                if self.write_word_fast(word):
                    # Kelimeyi asenkron oku
                    self.speak_async(word, speed=self.speech_speed * 1.2)
                    
                    self.clear_solenoids()
                    time.sleep(self.write_speed * 1.2)
                
                self.current_position += len(word) + 1
                
                if len(word) > 0 and (self.current_position % 50 < len(word)):
                    self.save_progress()
            
            time.sleep(0.02)
        
        if not self.is_paused:
            self.is_playing = False
            self.save_progress()
            self.speak("Mod bitti", speed=1.8)
    
    def mode_education(self):
        """Braille eğitim modu"""
        self.speak("Eğitim modu", speed=1.8)
        
        letters = [("a", "a"), ("b", "b"), ("c", "c")]
        
        for char, description in letters:
            if self.stop_event.is_set() or not self.is_playing:
                break
            
            while self.is_paused and self.is_playing and not self.stop_event.is_set():
                time.sleep(0.05)
            
            self.speak(description, speed=2.0)
            time.sleep(0.2)
            
            if char in self.braille_map:
                self.set_solenoids(self.braille_map[char])
                time.sleep(1.0)
                self.clear_solenoids()
                time.sleep(0.2)
        
        self.is_playing = False
        self.speak("Eğitim bitti", speed=1.8)
    
    # ==================== İLERLEME YÖNETİMİ ====================
    def load_progress(self):
        """İlerlemeyi yükle"""
        progress_file = f"{LOCAL_BOOKS_DIR}/progress.json"
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r', encoding='utf-8') as f:
                    self.progress_data = json.load(f)
            except:
                self.progress_data = {}
    
    def save_progress(self):
        """İlerlemeyi kaydet"""
        if not self.selected_book:
            return
        
        try:
            book_key = self.selected_book['filename']
            self.progress_data[book_key] = {
                'position': self.current_position,
                'mode': self.current_mode,
                'timestamp': time.time()
            }
            
            progress_file = f"{LOCAL_BOOKS_DIR}/progress.json"
            with open(progress_file, 'w', encoding='utf-8') as f:
                json.dump(self.progress_data, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    # ==================== ANA DÖNGÜ ====================
    def main_loop(self):
        """Ana program döngüsü"""
        try:
            while self.is_running:
                self.check_buttons()
                time.sleep(0.01)  # DAHA HIZLI KONTROL
                
        except KeyboardInterrupt:
            print("\n⏹️ Durduruldu")
            self.cleanup()
        except Exception as e:
            print(f"Hata: {e}")
            self.cleanup()
    
    def cleanup(self):
        """Temizlik"""
        self.is_running = False
        self.stop_event.set()
        self.is_playing = False
        
        self.voice_engine.stop()
        time.sleep(0.1)
        self.clear_solenoids()
        self.save_progress()
        GPIO.cleanup()
        print("✅ Sistem kapatıldı")

# ==================== ANA PROGRAM ====================
def main():
    """Ana fonksiyon"""
    print("=" * 60)
    print("🎵 BRAİLLE KİTAP OKUYUCU - OPTİMİZE PİPER TTS")
    print("=" * 60)
    print("🎯 OPTİMİZASYONLAR:")
    print("  • Ses kuyruğu ile anında tepki")
    print("  • Hızlı Piper parametreleri")
    print("  • Menü tek seferde konuşur")
    print("  • Tuş debounce mekanizması")
    print("  • Yüksek başlangıç hızı (1.5x)")
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
    except Exception as e:
        print(f"Hata: {e}")
        reader.cleanup()

if __name__ == "__main__":
    main()
