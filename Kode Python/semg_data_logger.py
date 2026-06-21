import socket
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import threading
import csv
import time
import os
from scipy.signal import welch
from collections import deque
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

# Variabel Sistem
HOST = '0.0.0.0'
PORT = 8080
SAMPLING_FREQ = 2500.0
WINDOW_SIZE = 500
TARGET_SAMPLES_MODE2 = int(SAMPLING_FREQ * 15) # 37.500 Sampel (Setara 15 Detik)
REST_DURATION_SEC = 60.0 # Waktu istirahat wajib antar repetisi (Detik)
PLOT_WINDOW_WIDTH = 12   
PLOT_WINDOW_HEIGHT = 7   
data_lock = threading.Lock()
record_start_time = 0.0
total_pause_time = 0.0
pause_start_time = 0.0
device_connected = False
program_mode = 1 
is_recording = False
is_paused = False
is_running = True
subject_name = "Unknown"
csv_filename = ""

# Variabel EMG
raw_signal_buffer = []
raw_signal_window = deque(maxlen=WINDOW_SIZE)
feature_calc_buffer = []
label_list = []

# Variabel Pengukuran Kelelahan Otot
current_label = -1
is_countdown_m1 = False
countdown_start_m1 = 0.0

# Variabel Pengukuran Berat Beban
load_list = []
total_sessions = 1
curr_load_idx = 0
curr_session = 1
target_load_kg = 0.0
mode2_state = "IDLE" 
state_start_time = 0.0

# Fungsi Durasi Pengukuran
def get_real_duration():
    global is_recording, is_paused, record_start_time, pause_start_time, total_pause_time
    if not is_recording: return 0.0
    if is_paused: return pause_start_time - record_start_time - total_pause_time
    return time.time() - record_start_time - total_pause_time

# Fungsi Ekstraksi Fitur
def calculate_features(data, fs):
    rms = np.sqrt(np.mean(data**2))
    mav = np.mean(np.abs(data))
    wl = np.sum(np.abs(np.diff(data)))
    iemg = np.sum(np.abs(data)) 
    var = np.var(data) 
    if len(data) > 0:
        zcr = np.sum(np.abs(np.diff(np.sign(data)))) / (2 * len(data))
    else:
        zcr = 0
    ssi = np.sum(data**2)
    freqs, psd = welch(data, fs=fs, nperseg=256)
    psd_sum = np.sum(psd)
    if psd_sum > 0:
        mnf = np.sum(freqs * psd) / psd_sum
        mdf = freqs[np.where(np.cumsum(psd) >= psd_sum / 2.0)[0][0]]
        peak_freq = freqs[np.argmax(psd)]
    else:
        mnf = 0; mdf = 0; peak_freq = 0
    return [rms, mav, wl, zcr, ssi, iemg, var, mnf, mdf, peak_freq, psd_sum]

# Fungsi Komunikasi TCP
def tcp_server_thread():
    global is_running, is_recording, device_connected, mode2_state
    global raw_signal_buffer, feature_calc_buffer, label_list, raw_signal_window
    global state_start_time, curr_session, curr_load_idx, target_load_kg
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) 
    server_socket.bind((HOST, PORT))
    server_socket.listen(1)
    print(f"\n[*] Menunggu koneksi dari alat di port {PORT}...")
    conn, addr = server_socket.accept()
    print(f"[+] Terhubung dengan Alat: {addr}")
    device_connected = True
    try:
        conn.sendall(b"MOS:1\n")
    except: pass
    data_residual = ""
    while is_running:
        try:
            packet = conn.recv(4096).decode('utf-8', errors='ignore')
            if not packet: break
            data_residual += packet
            while "\n" in data_residual:
                line, data_residual = data_residual.split("\n", 1)
                line = line.strip()
                if line.startswith("E1:"):
                    vals_str = line.split(":", 1)[1]
                    if not vals_str: continue     
                    voltages = [float(v) for v in vals_str.split(",") if v]
                    with data_lock:
                        for v in voltages:
                            raw_signal_window.append(v)
                            if is_recording and not is_paused:
                                ts_calc = len(raw_signal_buffer) / SAMPLING_FREQ 
                                if program_mode == 1:
                                    global current_label
                                    if current_label == 0 and ts_calc >= 30.0:
                                        current_label = 1
                                    label_to_save = current_label
                                else:
                                    label_to_save = target_load_kg
                                raw_signal_buffer.append(v)
                                feature_calc_buffer.append(v)
                                label_list.append(label_to_save)
                                if len(feature_calc_buffer) >= WINDOW_SIZE:
                                    try:
                                        window_data = np.array(feature_calc_buffer)
                                        features = calculate_features(window_data, SAMPLING_FREQ)
                                        with open(csv_filename, 'a', newline='') as f:
                                            writer = csv.writer(f)
                                            row = [subject_name, f"{ts_calc:.4f}", label_to_save]
                                            row.extend([f"{feat:.4f}" for feat in features])
                                            writer.writerow(row)
                                    except: pass
                                    feature_calc_buffer.clear()
                                if program_mode == 2 and len(raw_signal_buffer) >= TARGET_SAMPLES_MODE2:
                                    is_recording = False
                                    mode2_state = "RESTING"
                                    state_start_time = time.time()
                                    curr_session += 1
                                    if curr_session > total_sessions:
                                        curr_session = 1
                                        curr_load_idx += 1
                                        if curr_load_idx >= len(load_list):
                                            mode2_state = "DONE"
                                        else:
                                            target_load_kg = load_list[curr_load_idx]
        except Exception as e:
            if is_running: print(f"[-] Error koneksi: {e}")
            break    
    device_connected = False
    conn.close()
    server_socket.close()

# Fungsi Mengupdate UI
def update_plot(frame):
    global is_countdown_m1, countdown_start_m1, is_recording, is_paused, current_label
    global mode2_state, state_start_time
    with data_lock:
        local_connected = device_connected
        if not local_connected:
            status_text.set_text('STATUS : [ MENUNGGU KONEKSI ALAT... ]')
            status_text.set_color('orange')
            counter_text.set_text('')
            return status_text, counter_text
        # UI Pengukuran Kelelahan Otot
        if program_mode == 1:
            if is_countdown_m1:
                elapsed = time.time() - countdown_start_m1
                if elapsed >= 3.0:
                    is_countdown_m1 = False
                    is_recording = True
                    is_paused = False
                    current_label = 0
                    global record_start_time, total_pause_time
                    record_start_time = time.time()
                    total_pause_time = 0.0
                    raw_signal_buffer.clear()
                    label_list.clear()
                    feature_calc_buffer.clear()
                else:
                    count_val = 3 - int(elapsed)
                    status_text.set_text("STATUS : [ PERSIAPAN ]")
                    status_text.set_color('magenta')
                    counter_text.set_text(f"MULAI DALAM: {count_val}")
                    return status_text, counter_text
            if is_recording:
                pause_status = " (PAUSED)" if is_paused else ""
                current_real_time = get_real_duration()
                counter_text.set_text(f"DURASI: {current_real_time:.1f} s")
                label_names = {-1: "[ STANDBY ]", 0: "[ BASELINE RILEKS ]", 1: "[ SEGAR / CONTRACTION ]", 2: "[ LELAH / FATIGUE ]"}
                status_text.set_text(f"STATUS: {label_names[current_label]}{pause_status}")
                status_text.set_color('darkorange' if current_label == 0 else ('green' if current_label == 1 else 'red'))
            else:
                status_text.set_text('STATUS : [ ALAT TERHUBUNG ]')
                status_text.set_color('green')
                counter_text.set_text('DURASI: 0.0 s')
        # UI Pengukuran Berat Beban
        elif program_mode == 2:
            current_samples = len(raw_signal_buffer)
            if mode2_state == "IDLE":
                status_text.set_text(f"TARGET: BEBAN {target_load_kg} Kg  |  SESI KE-{curr_session} dari {total_sessions}")
                status_text.set_color('darkblue')
                counter_text.set_text("TEKAN [ 1 ] UNTUK MEMULAI")
                counter_text.set_color('green')
            elif mode2_state == "COUNTDOWN":
                elapsed = time.time() - state_start_time
                if elapsed >= 3.0:
                    mode2_state = "RECORDING"
                    is_recording = True
                    raw_signal_buffer.clear()
                    feature_calc_buffer.clear()
                else:
                    count_val = 3 - int(elapsed)
                    status_text.set_text(f"BERSIAP TAHAN BEBAN {target_load_kg} Kg")
                    status_text.set_color('magenta')
                    counter_text.set_text(f"MULAI DALAM: {count_val}")
                    counter_text.set_color('magenta')
            elif mode2_state == "RECORDING":
                status_text.set_text(f"MEREKAM BEBAN {target_load_kg} Kg  [Sesi {curr_session}/{total_sessions}]")
                status_text.set_color('red')
                counter_text.set_text(f"SAMPEL DATA: {current_samples} / {TARGET_SAMPLES_MODE2}")
                counter_text.set_color('red')
            elif mode2_state == "RESTING":
                elapsed = time.time() - state_start_time
                remaining = int(REST_DURATION_SEC - elapsed)
                if remaining <= 0:
                    mode2_state = "IDLE"
                else:
                    status_text.set_text("FASE PEMULIHAN OTOT")
                    status_text.set_color('darkorange')
                    counter_text.set_text(f"ISTIRAHAT: {remaining} DETIK LALU LANJUT")
                    counter_text.set_color('darkorange')
            elif mode2_state == "DONE":
                status_text.set_text("PEMBUATAN DATASET SELESAI!")
                status_text.set_color('green')
                counter_text.set_text("TEKAN [ 4 ] UNTUK KELUAR & SIMPAN")
                counter_text.set_color('green')   
    return status_text, counter_text

# Fungsi Tombol
def on_press(event):
    global is_running, device_connected, program_mode, is_recording, is_paused
    global is_countdown_m1, countdown_start_m1, mode2_state, state_start_time
    key = event.key if event.key else ''
    if key == '1':
        if not device_connected: return
        with data_lock:
            if program_mode == 1:
                if not is_recording and not is_countdown_m1:
                    is_countdown_m1 = True
                    countdown_start_m1 = time.time()
            elif program_mode == 2:
                if mode2_state == "IDLE":
                    mode2_state = "COUNTDOWN"
                    state_start_time = time.time()
                elif mode2_state == "RESTING":
                    # Opsional: Memaksa skip istirahat jika ditekan 1 saat istirahat
                    mode2_state = "IDLE"
    elif key == '2':
        with data_lock:
            if is_recording and program_mode == 1:
                global current_label
                current_label = 2
    elif key == '3':
        if program_mode == 1:
            with data_lock:
                global pause_start_time, total_pause_time
                is_paused = not is_paused
                if is_paused: pause_start_time = time.time()
                else: total_pause_time += (time.time() - pause_start_time)
    elif key == '4':
        is_running = False
        plt.close()

# Fungsi Plotter Sinyal EMG
def run_realtime_plotter():
    fig, ax = plt.subplots(figsize=(10, 5))
    def update(frame):
        with data_lock: data = list(raw_signal_window)
        ax.clear()
        ax.plot(data, color='cyan', linewidth=1)
        ax.set_ylim(-1650, 1650)
        ax.set_title("MONITOR EMG REAL-TIME (Tekan [ 4 ] untuk kembali)")
        ax.grid(True, alpha=0.3)
    fig.canvas.mpl_connect('key_press_event', on_press)
    ani = FuncAnimation(fig, update, interval=30, cache_frame_data=False)
    plt.show()

# Fungsi Utama Sistem (Main Menu)
while True:
    os.system('cls' if os.name == 'nt' else 'clear')
    print("=================================================================")
    print("         SISTEM EVALUASI DAN EKSTRAKSI FITUR SINYAL SEMg         ")
    print("=================================================================")
    print("Pilih Mode Operasi:")
    print("0. Melihat Sinyal EMG Real-Time")
    print("1. Ekstraksi Fitur Pengukuran Kelalahan Otot")
    print("2. Ekstraksi Fitur Pengukuran Berat Beban")
    print("9. Keluar")
    mode_input = input("Masukkan Pilihan Mode: ").strip()
    if mode_input == '9': break
    program_mode = int(mode_input)
    raw_signal_buffer = []
    feature_calc_buffer = []
    label_list = []
    load_list = []
    curr_session = 1
    curr_load_idx = 0
    mode2_state = "IDLE"
    is_recording = False
    subject_name = "Unknown"
    is_running = True
    tcp_thread = threading.Thread(target=tcp_server_thread, daemon=True)
    tcp_thread.start()
    if mode_input == '9': 
        print("[*] Program Berakhir.")
        break
    if program_mode == 0:
        run_realtime_plotter()
    else:
        subject_name = input("Masukkan Nama Subjek: ").strip() or "Subjek_Unknown"
        if program_mode == 1:
            csv_filename = "semg_fatigue_log.csv"
            print(f"\n[+] MODE FATIGUE AKTIF. Data disimpan di: {csv_filename}")
        if program_mode == 2:
            csv_filename = "semg_weight_dataset.csv"
            print("\n--- KONFIGURASI SEQUENCE ML ---")
            while True:
                try:
                    val = input("Berapa sesi (repetisi) per beban? (Rekomendasi ideal: 5 - 7 sesi): ").strip()
                    total_sessions = int(val)
                    if total_sessions > 0: break
                except: print("[-] Masukkan angka yang valid!")
            print("\nMasukkan daftar berat beban (Kg) yang akan diuji")
            print("Ketik 'ok' jika semua beban sudah dimasukkan")
            while True:
                val = input(f"Beban ke-{len(load_list)+1} (Kg) [atau 'ok']: ").strip().lower()
                if val == 'ok':
                    if len(load_list) > 0: break
                    else: print("[-] Minimal masukkan 1 beban!")
                else:
                    try:
                        load_list.append(float(val))
                    except: print("[-] Angka tidak valid!")
            target_load_kg = load_list[0]
            print(f"\n[+] SEQUENCE DIBUAT: {len(load_list)} Beban {load_list} x {total_sessions} Sesi.")
            print(f"[+] Total Perekaman: {len(load_list) * total_sessions} kali. Berhenti di {TARGET_SAMPLES_MODE2} sampel per rekaman.")
        if not os.path.isfile(csv_filename):
            with open(csv_filename, mode='w', newline='') as f:
                writer = csv.writer(f)
                label_header = "Label_Class" if program_mode == 1 else "Load_Kg"
                writer.writerow([
                    "Subject_Name", "Timestamp", label_header, 
                    "RMS", "MAV", "WL", "ZCR", "SSI", "IEMG", "VAR", "MNF", "MDF", "PeakFreq", "TotalPower"
                ])
        fig, ax = plt.subplots(figsize=(PLOT_WINDOW_WIDTH, PLOT_WINDOW_HEIGHT))
        ax.axis('off')
        title_str = "DASHBOARD EVALUASI MUSCLE FATIGUE" if program_mode == 1 else "AUTO-SEQUENCE DATASET BEBAN"
        ax.text(0.5, 0.92, title_str, transform=ax.transAxes, fontsize=16, fontweight='bold', ha='center', color='black')
        status_text = ax.text(0.5, 0.65, 'STATUS : [ MENUNGGU KONEKSI ALAT... ]', transform=ax.transAxes, fontsize=18, fontweight='bold', ha='center', color='orange')
        counter_text = ax.text(0.5, 0.45, 'MEMUAT...', transform=ax.transAxes, fontsize=26, fontweight='bold', ha='center', color='blue')
        if program_mode == 1:
            instr_text = (
                "KONTROL SESI FATIGUE:\n"
                "1. Tekan [ 1 ] : Mulai Sesi (Ada Countdown 3 Detik).\n"
                "2. Detik 30    : (Otomatis) Subjek mulai menahan beban.\n"
                "3. Tekan [ 2 ] : Tekan saat subjek menyatakan otot lelah (Fatigue).\n"
                "4. Tekan [ 4 ] : Selesai, amankan data, dan tampilkan spektrum."
            )
        if program_mode == 2:
            instr_text = (
                "KONTROL AUTO-SEQUENCE ML:\n"
                "1. Ikuti instruksi 'TARGET BEBAN' di layar utama.\n"
                "2. Tekan [ 1 ] untuk memulai hitung mundur dan merekam beban.\n"
                "3. Perekaman OTOMATIS BERHENTI, lalu masuk ke mode ISTIRAHAT.\n"
                "4. Tunggu istirahat selesai, lalu tekan [ 1 ] lagi untuk sesi berikutnya.\n"
                "(Tekan [ 1 ] saat fase istirahat jika ingin men-skip timer istirahat)."
            )     
        ax.text(0.05, 0.04, instr_text, transform=ax.transAxes, fontsize=10, ha='left', va='bottom', bbox=dict(facecolor='lightcyan', alpha=0.95, edgecolor='blue', pad=12))
        fig.canvas.mpl_connect('key_press_event', on_press)
        tcp_thread = threading.Thread(target=tcp_server_thread, daemon=True)
        tcp_thread.start()
        ani = FuncAnimation(fig, update_plot, interval=50, blit=True, cache_frame_data=False)
        plt.show(block=True)
        is_running = False
        tcp_thread.join(timeout=1.0)
        device_connected = False
