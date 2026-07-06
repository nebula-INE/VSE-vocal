# modules/tools/batch_voice_optimizer.py
# 完全版：音素認識＋精密フォルマント＋機械学習予測によるoto.ini自動生成

import os
import json
import hashlib
import pickle
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple, Any
import multiprocessing

import numpy as np
import soundfile as sf
from scipy import signal
from scipy.fft import rfft, rfftfreq
from scipy.signal import find_peaks, butter, filtfilt
from scipy.linalg import toeplitz, solve  # LPC計算用に追記
from sklearn.ensemble import RandomForestRegressor
from sklearn.preprocessing import StandardScaler

try:
    import pyopenjtalk
    PYOPENJTALK_AVAILABLE = True
except ImportError:
    PYOPENJTALK_AVAILABLE = False

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


# ============================================================
# 1. データクラス
# ============================================================

@dataclass
class OtoParams:
    """oto.ini パラメータ（ms単位）"""
    offset: float
    preutter: float
    overlap: float
    constant: float
    blank: float
    confidence: float = 0.0
    phoneme: str = ""
    consonant_type: str = ""


@dataclass
class AcousticFeatures:
    """WAVから抽出する音響特徴量（機械学習用）"""
    onset_time: float
    attack_time: float
    decay_time: float
    release_time: float
    rms_max: float
    rms_mean: float
    rms_std: float
    zcr_mean: float
    zcr_std: float
    centroid_mean: float
    centroid_std: float
    bandwidth_mean: float
    rolloff_mean: float
    flatness_mean: float
    f1_mean: float
    f1_std: float
    f2_mean: float
    f2_std: float
    f1_f2_ratio: float
    spectral_flux_mean: float
    spectral_flux_std: float
    phoneme: str = ""
    consonant_type: str = ""
    vowel: str = ""


# ============================================================
# 2. 高精度フォルマントトラッカー（LPCバグ修正版）
# ============================================================

class FormantTracker:
    def __init__(self, sr: int = 16000):
        self.sr = sr
        self.n_formants = 4
        self.formant_ranges = [
            (200, 1000),   # F1
            (500, 2000),   # F2
            (1500, 3500),  # F3
            (3000, 5000),  # F4
        ]

    @staticmethod
    def _scipy_lpc(x: np.ndarray, order: int) -> np.ndarray:
        """SciPy/NumPyでYule-Walker方程式を解きLPC係数を計算する自作関数"""
        if len(x) < order:
            return np.zeros(order + 1)
        # 自己相関
        r = np.correlate(x, x, mode='full')[len(x) - 1:len(x) + order]
        if r[0] == 0:
            return np.zeros(order + 1)
        R = toeplitz(r[:-1])
        try:
            a = solve(R, -r[1:], check_finite=False)
        except Exception:
            return np.zeros(order + 1)
        return np.concatenate(([1.0], a))

    def track(self, x: np.ndarray, frame_len: int = 512, hop: int = 128) -> np.ndarray:
        n_frames = max(1, (len(x) - frame_len) // hop + 1)
        formants = np.zeros((n_frames, self.n_formants))
        
        for i in range(n_frames):
            start = i * hop
            end = start + frame_len
            if end > len(x):
                break
            frame = x[start:end] * np.hanning(frame_len)
            formants[i] = self._track_frame(frame)
        
        for f in range(self.n_formants):
            if len(formants) > 5:
                formants[:, f] = signal.medfilt(formants[:, f], kernel_size=5)
        return formants
    
    def _track_frame(self, frame: np.ndarray) -> np.ndarray:
        lpc_order = 12
        a = self._scipy_lpc(frame, lpc_order)
        if np.all(a == 0):
            return np.zeros(self.n_formants)
        
        n_fft = 1024
        w, h = signal.freqz(1, a, n_fft, fs=self.sr)
        spec_db = 20 * np.log10(np.abs(h) + 1e-10)
        
        peaks, props = find_peaks(spec_db, height=-30, distance=5)
        peak_freqs = w[peaks]
        peak_heights = spec_db[peaks]
        
        selected = []
        for f_min, f_max in self.formant_ranges:
            mask = (peak_freqs >= f_min) & (peak_freqs <= f_max)
            candidates = peak_freqs[mask]
            heights = peak_heights[mask]
            if len(candidates) > 0:
                selected.append(candidates[np.argmax(heights)])
            else:
                selected.append(0.0)
        return np.array(selected)


# ============================================================
# 3. 音素認識エンジン
# ============================================================

class PhonemeRecognizer:
    def __init__(self, use_g2p: bool = True):
        self.use_g2p = use_g2p and PYOPENJTALK_AVAILABLE
        self.consonant_types = {
            'k': 'unvoiced_stop', 't': 'unvoiced_stop', 'p': 'unvoiced_stop',
            's': 'fricative', 'h': 'fricative', 
            'ts': 'affricate', 'ch': 'affricate', 'j': 'affricate',
            'b': 'voiced_stop', 'd': 'voiced_stop', 'g': 'voiced_stop',
            'm': 'nasal', 'n': 'nasal', 'N': 'nasal',
            'r': 'liquid', 'w': 'approximant', 'y': 'approximant', 'cl': 'special'
        }
    
    def recognize(self, x: np.ndarray, sr: int, filename: str = "") -> AcousticFeatures:
        phoneme = 'a'
        consonant_type = 'vowel'
        vowel = 'a'
        
        if self.use_g2p and filename:
            try:
                base = os.path.splitext(os.path.basename(filename))[0]
                result = pyopenjtalk.g2p(base, kana=False)
                if result:
                    parts = result.split()
                    if parts:
                        first = parts[0]
                        vowels = [p for p in parts if p in 'aiueo']
                        vowel = vowels[-1] if vowels else 'a'
                        consonant = first if first not in 'aiueo' else ''
                        consonant_type = self.consonant_types.get(consonant, 'vowel' if not consonant else 'unvoiced_stop')
                        phoneme = first
            except Exception:
                pass
        
        features = self._extract_acoustic_features(x, sr)
        features.phoneme = phoneme
        features.consonant_type = consonant_type
        features.vowel = vowel
        return features
    
    def _extract_acoustic_features(self, x: np.ndarray, sr: int) -> AcousticFeatures:
        frame_len = int(sr * 0.025)
        hop_len = int(sr * 0.010)
        n_frames = max(1, (len(x) - frame_len) // hop_len + 1)
        
        rms = np.zeros(n_frames)
        zcr = np.zeros(n_frames)
        spectral_centroids = np.zeros(n_frames)
        spectral_bandwidths = np.zeros(n_frames)
        spectral_rolloffs = np.zeros(n_frames)
        spectral_flux = np.zeros(n_frames)
        prev_fft = None
        
        for i in range(n_frames):
            start = i * hop_len
            end = min(start + frame_len, len(x))
            seg = x[start:end]
            if len(seg) < frame_len:
                seg = np.pad(seg, (0, frame_len - len(seg)))
            
            rms[i] = np.sqrt(np.mean(seg ** 2))
            zcr[i] = np.sum(np.abs(np.diff(np.sign(seg)))) / (2 * len(seg)) if len(seg) > 0 else 0
            
            windowed = seg * np.hanning(frame_len)
            fft_vals = np.abs(rfft(windowed))
            freqs = rfftfreq(frame_len, d=1/sr)
            sum_fft = np.sum(fft_vals)
            
            if sum_fft > 0:
                spectral_centroids[i] = np.sum(freqs * fft_vals) / sum_fft
                spectral_bandwidths[i] = np.sqrt(np.sum((freqs - spectral_centroids[i]) ** 2 * fft_vals) / sum_fft)
                cumsum = np.cumsum(fft_vals)
                spectral_rolloffs[i] = freqs[np.argmax(cumsum >= 0.85 * cumsum[-1])]
            if prev_fft is not None:
                spectral_flux[i] = np.sum((fft_vals - prev_fft) ** 2)
            prev_fft = fft_vals.copy()
            
        envelope = np.abs(signal.hilbert(x)) if len(x) > 0 else np.zeros_like(x)
        env_smooth = np.convolve(envelope, np.ones(int(sr*0.01))/int(sr*0.01), mode='same')
        env_diff = np.diff(env_smooth, prepend=0)
        env_accel = np.diff(env_diff, prepend=0)
        
        search_len = min(int(sr * 0.1), len(env_accel))
        onset_idx = np.argmax(env_accel[:search_len]) if search_len > 0 else 0
        peak_idx = np.argmax(env_smooth)
        attack_time = (peak_idx - onset_idx) / sr if peak_idx > onset_idx else 0.0
        
        release_idx = peak_idx
        for i in range(peak_idx, len(env_smooth)):
            if env_smooth[i] < env_smooth[peak_idx] * 0.368:
                release_idx = i
                break
        release_time = (release_idx - peak_idx) / sr
        
        tracker = FormantTracker(sr)
        formants = tracker.track(x)
        f1_vals = formants[:, 0]
        f2_vals = formants[:, 1]
        f1_mean = np.mean(f1_vals[f1_vals > 0]) if np.any(f1_vals > 0) else 0.0
        f2_mean = np.mean(f2_vals[f2_vals > 0]) if np.any(f2_vals > 0) else 0.0
        f1_std = np.std(f1_vals[f1_vals > 0]) if np.any(f1_vals > 0) else 0.0
        f2_std = np.std(f2_vals[f2_vals > 0]) if np.any(f2_vals > 0) else 0.0
        
        return AcousticFeatures(
            onset_time=onset_idx / sr, attack_time=attack_time, decay_time=0.0, release_time=release_time,
            rms_max=np.max(rms) if len(rms)>0 else 0, rms_mean=np.mean(rms) if len(rms)>0 else 0, rms_std=np.std(rms) if len(rms)>0 else 0,
            zcr_mean=np.mean(zcr) if len(zcr)>0 else 0, zcr_std=np.std(zcr) if len(zcr)>0 else 0,
            centroid_mean=np.mean(spectral_centroids), centroid_std=np.std(spectral_centroids),
            bandwidth_mean=np.mean(spectral_bandwidths), rolloff_mean=np.mean(spectral_rolloffs),
            flatness_mean=np.mean(spectral_centroids / (spectral_bandwidths + 1e-10)),
            f1_mean=f1_mean, f1_std=f1_std, f2_mean=f2_mean, f2_std=f2_std, f1_f2_ratio=f1_mean / (f2_mean + 1e-10),
            spectral_flux_mean=np.mean(spectral_flux), spectral_flux_std=np.std(spectral_flux)
        )


# ============================================================
# 4. 機械学習予測モデル
# ============================================================

class OtoPredictor:
    def __init__(self, model_path: Optional[str] = None):
        self.model = None
        self.scaler = None
        self.is_trained = False
        if model_path and os.path.exists(model_path):
            self.load(model_path)
    
    def train(self, features: List[AcousticFeatures], labels: List[OtoParams]) -> None:
        if len(features) < 10:
            raise ValueError("学習には少なくとも10サンプル以上が必要です")
        X = self._features_to_vector(features)
        y = np.array([[l.offset, l.preutter, l.overlap, l.constant, l.blank] for l in labels])
        
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)
        self.model = RandomForestRegressor(n_estimators=100, max_depth=15, n_jobs=-1, random_state=42)
        self.model.fit(X_scaled, y)
        self.is_trained = True
    
    def predict(self, features: AcousticFeatures) -> OtoParams:
        if not self.is_trained or self.model is None:
            return self._heuristic_predict(features)
        X = self._features_to_vector([features])
        X_scaled = self.scaler.transform(X)
        pred = self.model.predict(X_scaled)[0]
        return OtoParams(
            offset=float(np.clip(pred[0], 0, 200)), preutter=float(np.clip(pred[1], 10, 350)),
            overlap=float(np.clip(pred[2], 0, 150)), constant=float(np.clip(pred[3], 0, 300)),
            blank=float(np.clip(pred[4], -200, 0)), confidence=0.8,
            phoneme=features.phoneme, consonant_type=features.consonant_type
        )
    
    def save(self, path: str) -> None:
        if self.model is None or self.scaler is None: return
        with open(path, 'wb') as f:
            pickle.dump({'model': self.model, 'scaler': self.scaler}, f)
    
    def load(self, path: str) -> None:
        with open(path, 'rb') as f:
            data = pickle.load(f)
        self.model = data['model']
        self.scaler = data['scaler']
        self.is_trained = True
    
    def _features_to_vector(self, features: List[AcousticFeatures]) -> np.ndarray:
        return np.array([[
            f.onset_time, f.attack_time, f.release_time, f.rms_max, f.rms_mean, f.rms_std,
            f.zcr_mean, f.zcr_std, f.centroid_mean, f.centroid_std, f.bandwidth_mean, f.rolloff_mean, f.flatness_mean,
            f.f1_mean, f.f1_std, f.f2_mean, f.f2_std, f.f1_f2_ratio, f.spectral_flux_mean, f.spectral_flux_std
        ] for f in features], dtype=np.float64)
    
    def _heuristic_predict(self, features: AcousticFeatures) -> OtoParams:
        ctype = features.consonant_type
        base_params = {
            'unvoiced_stop': (30, 120, 45, 180, -10), 'voiced_stop': (20, 100, 40, 150, -10),
            'fricative': (10, 80, 25, 100, -15), 'affricate': (25, 110, 40, 160, -10),
            'nasal': (15, 90, 30, 120, -15), 'liquid': (10, 70, 20, 90, -15),
            'approximant': (5, 60, 15, 80, -20), 'vowel': (0, 50, 10, 50, -20), 'special': (0, 20, 0, 20, -10),
        }
        offset, preutter, overlap, constant, blank = base_params.get(ctype, (20, 100, 40, 150, -10))
        if features.vowel == 'i': preutter *= 0.9
        elif features.vowel == 'u': preutter *= 0.85
        return OtoParams(offset, preutter, overlap, constant, blank, 0.5, features.phoneme, ctype)


# ============================================================
# 5. メイン最適化エンジン（並列バグ修正版）
# ============================================================

class BatchVoiceOptimizer:
    def __init__(self, target_sr: int = 16000, cache_dir: str = "cache/oto_cache"):
        self.target_sr = target_sr
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.model_path = os.path.join(cache_dir, "oto_predictor.pkl")
        self.training_features: List[AcousticFeatures] = []
        self.training_labels: List[OtoParams] = []
    
    def optimize_voice_bank(self, voice_dir: str, force_redo: bool = False) -> Dict[str, OtoParams]:
        wav_files = self._collect_wavs(voice_dir)
        if not wav_files: return {}
        
        tasks = []
        for wav_path in wav_files:
            cache_key = self._get_cache_key(wav_path)
            cache_path = os.path.join(self.cache_dir, f"{cache_key}.json")
            if not force_redo and os.path.exists(cache_path):
                if os.path.getmtime(wav_path) <= os.path.getmtime(cache_path):
                    continue
            tasks.append(wav_path)
        
        if not tasks:
            return self._load_all_caches(wav_files)
        
        print(f"[BatchOptimizer] Analyzing {len(tasks)} / {len(wav_files)} WAVs...")
        
        # モデルが存在するかどうかを事前に確認してパスを引き渡す
        current_model = self.model_path if os.path.exists(self.model_path) else None
        results: Dict[str, OtoParams] = {}
        
        with ProcessPoolExecutor(max_workers=multiprocessing.cpu_count()) as executor:
            # 引数に current_model を追加して、子プロセス側でロードできるように変更
            future_to_path = {
                executor.submit(self._analyze_single_wav, path, self.target_sr, current_model): path
                for path in tasks
            }
            for future in as_completed(future_to_path):
                path = future_to_path[future]
                try:
                    params, features = future.result(timeout=60)
                    results[path] = params
                    self._save_cache(path, params)
                    if params.confidence > 0.7:
                        self.training_features.append(features)
                        self.training_labels.append(params)
                except Exception as e:
                    print(f"[ERROR] Failed to analyze {path}: {e}")
        
        if len(self.training_features) >= 20:
            print(f"[BatchOptimizer] Updating ML model with {len(self.training_features)} samples...")
            try:
                predictor = OtoPredictor(self.model_path if os.path.exists(self.model_path) else None)
                predictor.train(self.training_features, self.training_labels)
                predictor.save(self.model_path)
            except Exception as e:
                print(f"[BatchOptimizer] Model training failed: {e}")
        
        cached_results = self._load_all_caches(wav_files)
        results.update(cached_results)
        return results
    
    @staticmethod
    def _analyze_single_wav(wav_path: str, target_sr: int, model_path: Optional[str]) -> Tuple[OtoParams, AcousticFeatures]:
        """1WAVを解析（子プロセス用スタティックメソッド。モデルパスを受け取るように修正）"""
        data, sr = sf.read(wav_path, always_2d=False)
        x = np.asarray(data, dtype=np.float64)
        if x.ndim > 1:
            x = np.mean(x, axis=1)
        if sr != target_sr:
            x = signal.resample(x, int(len(x) * target_sr / sr))
            sr = target_sr
        
        recognizer = PhonemeRecognizer(use_g2p=PYOPENJTALK_AVAILABLE)
        features = recognizer.recognize(x, sr, wav_path)
        
        # 親プロセスから指定された正確なモデルファイルをロードして予測
        predictor = OtoPredictor(model_path)
        params = predictor.predict(features)
        
        params = BatchVoiceOptimizer._refine_params(x, sr, params, features)
        return params, features
    
    @staticmethod
    def _refine_params(x: np.ndarray, sr: int, params: OtoParams, features: AcousticFeatures) -> OtoParams:
        if len(x) == 0: return params
        envelope = np.abs(signal.hilbert(x))
        env_smooth = np.convolve(envelope, np.ones(int(sr*0.005))/int(sr*0.005), mode='same')
        env_diff = np.diff(env_smooth, prepend=0)
        env_accel = np.diff(env_diff, prepend=0)
        
        search_len = min(int(sr * 0.05), len(env_accel))
        if search_len > 0:
            actual_onset = np.argmax(np.abs(env_accel[:search_len]))
            params.offset = (actual_onset / sr) * 1000.0
        
        peak_idx = np.argmax(env_smooth)
        if features.f1_mean > 0:
            search_start = max(0, int(params.offset * sr / 1000.0))
            search_end = min(peak_idx + int(sr*0.05), len(x))
            if search_end > search_start:
                rms_vals = []
                frame_len = int(sr * 0.01)
                for i in range(search_start, search_end, frame_len):
                    seg = x[i:i+frame_len]
                    if len(seg) > 0:
                        rms_vals.append(np.sqrt(np.mean(seg**2)))
                if rms_vals:
                    rms_arr = np.array(rms_vals)
                    idx_80 = np.argmax(rms_arr >= 0.8 * np.max(rms_arr))
                    stable_ms = (search_start + idx_80 * frame_len) / sr * 1000
                    params.preutter = float(np.clip(stable_ms, 10, 350))
        return params

    def _get_cache_key(self, path: str) -> str:
        return hashlib.md5(path.encode('utf-8')).hexdigest()
    
    def _save_cache(self, wav_path: str, params: OtoParams) -> None:
        cache_key = self._get_cache_key(wav_path)
        cache_path = os.path.join(self.cache_dir, f"{cache_key}.json")
        with open(cache_path, 'w') as f:
            json.dump(asdict(params), f)
    
    def _load_cache(self, wav_path: str) -> Optional[OtoParams]:
        cache_key = self._get_cache_key(wav_path)
        cache_path = os.path.join(self.cache_dir, f"{cache_key}.json")
        if not os.path.exists(cache_path): return None
        try:
            with open(cache_path, 'r') as f:
                data = json.load(f)
            return OtoParams(**data)
        except Exception:
            return None
    
    def _load_all_caches(self, wav_files: List[str]) -> Dict[str, OtoParams]:
        results = {}
        for path in wav_files:
            cached = self._load_cache(path)
            if cached: results[path] = cached
        return results
    
    @staticmethod
    def _collect_wavs(voice_dir: str) -> List[str]:
        wavs = []
        for root, _, files in os.walk(voice_dir):
            for f in files:
                if f.lower().endswith('.wav'):
                    wavs.append(os.path.join(root, f))
        return wavs
    
    @staticmethod
    def export_oto_ini(voice_dir: str, params_map: Dict[str, OtoParams], output_name: str = "oto.ini") -> None:
        oto_path = os.path.join(voice_dir, output_name)
        lines = []
        for wav_path, p in params_map.items():
            fname = os.path.basename(wav_path)
            alias = os.path.splitext(fname)[0]
            line = (f"{fname}={alias},"
                    f"{p.offset:.0f},{p.constant:.0f},"
                    f"{p.blank:.0f},{p.preutter:.0f},{p.overlap:.0f}")
            lines.append(line)
        with open(oto_path, 'w', encoding='cp932', errors='replace') as f:
            f.write("\n".join(lines))
        print(f"[BatchOptimizer] Exported {len(lines)} entries to {oto_path}")
