#graph_editor_widget.py

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, Slot, QRect, QPointF
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPaintEvent, QMouseEvent, QPainterPath
from modules.data.data_models import PitchEvent
import bisect
from typing import Optional, List, Dict

import logging
logger = logging.getLogger(__name__)

class GraphEditorWidget(QWidget):
    parameters_changed = Signal(dict) 

    PITCH_MAX = 8191
    PITCH_MIN = -8192

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        
        self.scroll_x_offset = 0.0
        self.pixels_per_beat = 40.0
        self.tempo = 120.0
        
        # 代表が追加した WORLD 用のパラメータ構成を維持
        self.all_parameters: Dict[str, List[PitchEvent]] = {
            "Pitch": [],
            "Gender": [],
            "Tension": [],
            "Breath": []
        }
        
        self.current_mode = "Pitch"
        self.colors = {
            "Pitch": QColor(0, 255, 127),      # ネオングリーン
            "Gender": QColor(231, 76, 60),     # ソフトレッド
            "Tension": QColor(46, 204, 113),    # エメラルド
            "Breath": QColor(241, 196, 15)      # サンフラワー
        }

        self.editing_point_index: Optional[int] = None
        self.hover_point_index: Optional[int] = None

        logger.info("GraphEditorWidget initialized successfully.")

        # --- Compatibility methods (called from MainWindow) ---
    def set_horizontal_offset(self, offset: int) -> None:
        """タイムラインの水平スクロールと同期。"""
        try:
            self.scroll_x_offset = float(offset)
            self.update()
        except Exception:
            pass

    def set_vertical_offset(self, offset: int) -> None:
        """現状のGraphEditorでは未使用だが互換のため保持。"""
        # 縦スクロール同期が必要になった時の拡張ポイント
        _ = offset

    def sync_with_notes(self, notes: Optional[list] = None) -> None:
        """MainWindow互換: ノート変更時の同期フック。"""
        # 現時点ではグラフ側で直接ノート保持していないため no-op
        _ = notes
        self.update()

    def get_value_at_time(self, events: list, t: float) -> float:
        """MainWindow互換: 指定時刻のパラメータ値を返す。"""
        if not events:
            return 0.0
        try:
            # eventsはtime昇順前提。直前値を返す簡易実装
            last_val = float(getattr(events[0], "value", 0.0))
            for ev in events:
                ev_t = float(getattr(ev, "time", 0.0))
                ev_v = float(getattr(ev, "value", last_val))
                if ev_t > t:
                    break
                last_val = ev_v
            return last_val
        except Exception:
            return 0.0

    @Slot(str)
    def set_mode(self, mode: str):
        if mode in self.all_parameters:
            self.current_mode = mode
            self.editing_point_index = None
            self.update()

    def time_to_x(self, seconds: float) -> float:
        beats = (seconds * self.tempo) / 60.0
        return float((beats * self.pixels_per_beat) - self.scroll_x_offset)

    def x_to_time(self, x: float) -> float:
        absolute_x = x + self.scroll_x_offset
        beats = absolute_x / self.pixels_per_beat
        return float((beats * 60.0) / self.tempo)

    def value_to_y(self, value: float) -> float:
        h = float(self.height())
        if self.current_mode == "Pitch":
            center_y = h / 2.0
            range_y = center_y * 0.8
            return center_y - (value / self.PITCH_MAX) * range_y
        else:
            return h - (value * (h * 0.8) + (h * 0.1))

    def y_to_value(self, y: float) -> float:
        h = float(self.height())
        if self.current_mode == "Pitch":
            center_y = h / 2.0
            range_y = center_y * 0.8
            val = -((y - center_y) / range_y) * self.PITCH_MAX
            return float(max(self.PITCH_MIN, min(self.PITCH_MAX, val)))
        else:
            val = (h - y - (h * 0.1)) / (h * 0.8)
            return float(max(0.0, min(1.0, val)))

    def value_to_y_for_mode(self, value: float, mode: str) -> float:
        h = float(self.height())
        if mode == "Pitch":
            center_y = h / 2.0
            return center_y - (value / self.PITCH_MAX) * (center_y * 0.8)
        else:
            return h - (value * (h * 0.8) + (h * 0.1))

    def _get_point_at_pos(self, pos: QPointF, events: list) -> int | None:
        """
        [O(log N) 高速探索アルゴリズム]
        総当たり(O(N))を廃止し、クリックされた時間(X座標)周辺の点のみをピンポイントで検証する。
        """
        if not events:
            return None
            
        click_time = self.x_to_time(pos.x())
        
        # bisectを使って、クリックされた時間が挿入されるべきインデックスを高速に特定
        idx = bisect.bisect_left(events, click_time, key=lambda e: e.time)
        
        # ピクセルの当たり判定（16x16）を考慮し、前後数個の点だけを調べる
        start_idx = max(0, idx - 2)
        end_idx = min(len(events), idx + 2)
        
        for i in range(start_idx, end_idx):
            p = events[i]
            px = self.time_to_x(p.time)
            py = self.value_to_y(p.value)
            if QRect(int(px)-8, int(py)-8, 16, 16).contains(pos.toPoint()):
                return i
        return None

    def mouseDoubleClickEvent(self, event):
        """
        ダブルクリックによる制御点の追加。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            # position() から確実に座標を取得
            pos: QPointF = event.position()
            
            # x_to_time 等の戻り値を明示的に float キャスト（pyright対策）
            time_val: float = float(self.x_to_time(pos.x()))
            param_val: float = float(self.y_to_value(pos.y()))
            
            # [解決] 引数として time_val と param_val を渡します
            new_point = PitchEvent(time=time_val, value=param_val)
            
            current_list = self.all_parameters.get(self.current_mode)
            
            if current_list is not None:
                # 1ms以内の既存点を削除（上書き動作）
                current_list[:] = [p for p in current_list if abs(p.time - time_val) > 0.001]
                
                # リストに追加してソート
                current_list.append(new_point)
                current_list.sort(key=lambda x: x.time)
                
                # 変更通知
                self.parameters_changed.emit(self.all_parameters)
                self.update()
                
                # [解決] 定義済みの logger を使用
                logger.debug(f"Point added at t={time_val:.3f}, v={param_val:.3f}")
                

    def mousePressEvent(self, event: QMouseEvent):
        pos = event.position()
        events = self.all_parameters[self.current_mode]
        
        if event.button() == Qt.MouseButton.LeftButton:
            self.editing_point_index = self._get_point_at_pos(pos, events)
            
        elif event.button() == Qt.MouseButton.RightButton:
            target_idx = self._get_point_at_pos(pos, events)
            if target_idx is not None:
                events.pop(target_idx)
                self.parameters_changed.emit(self.all_parameters)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()
        events = self.all_parameters[self.current_mode]
        
        # ドラッグ中（点の移動）
        if event.buttons() & Qt.MouseButton.LeftButton and self.editing_point_index is not None:
            p = events[self.editing_point_index]
            p.time = max(0.0, self.x_to_time(pos.x()))
            p.value = self.y_to_value(pos.y())
            self.parameters_changed.emit(self.all_parameters)
        
        # ホバー判定（高速探索）
        self.hover_point_index = self._get_point_at_pos(pos, events)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.editing_point_index is not None:
            # ドラッグ終了時に時間軸の順序が狂う可能性があるため再ソート
            self.all_parameters[self.current_mode].sort(key=lambda x: x.time)
            self.editing_point_index = None
            self.update()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Apple風の深みのあるグレー背景
        painter.fillRect(self.rect(), QColor(30, 30, 30))

        h = float(self.height())
        w = float(self.width())

        # センターライン
        if self.current_mode == "Pitch":
            painter.setPen(QPen(QColor(60, 60, 60), 1, Qt.PenStyle.DashLine))
            painter.drawLine(0, int(h/2), int(w), int(h/2))

        # --- [1. 背景パラメータの一括描画 (QPainterPath)] ---
        for mode, events in self.all_parameters.items():
            if mode == self.current_mode or not events:
                continue
                
            color = QColor(self.colors[mode])
            color.setAlpha(30)
            painter.setPen(QPen(color, 1))
            
            path = QPainterPath()
            first_p = events[0]
            path.moveTo(self.time_to_x(first_p.time), self.value_to_y_for_mode(first_p.value, mode))
            for p in events[1:]:
                path.lineTo(self.time_to_x(p.time), self.value_to_y_for_mode(p.value, mode))
            painter.drawPath(path)

        # --- [2. アクティブパラメータの一括描画 (QPainterPath)] ---
        events = self.all_parameters[self.current_mode]
        color = self.colors[self.current_mode]
        
        if len(events) >= 2:
            painter.setPen(QPen(color, 2))
            path = QPainterPath()
            first_p = events[0]
            path.moveTo(self.time_to_x(first_p.time), self.value_to_y(first_p.value))
            for p in events[1:]:
                path.lineTo(self.time_to_x(p.time), self.value_to_y(p.value))
            # GPUに「このパスをまとめて描け」と一度だけ命令する（超高速）
            painter.drawPath(path)

        # --- [3. コントロールポイントの描画] ---
        # 画面に映っている点だけを描画するようにクリッピングするとなお良いですが、
        # 今回はQPointFの描画コストが低いためそのまま描画します。
        for i, p in enumerate(events):
            px = self.time_to_x(p.time)
            py = self.value_to_y(p.value)
            
            if i == self.hover_point_index:
                painter.setBrush(QBrush(QColor(255, 255, 255)))
                painter.setPen(QPen(color, 2))
                radius = 6
            else:
                painter.setBrush(QBrush(color))
                painter.setPen(Qt.PenStyle.NoPen)
                radius = 4
                
            painter.drawEllipse(QPointF(px, py), radius, radius)
