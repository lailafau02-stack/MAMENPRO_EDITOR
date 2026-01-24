import os
import tempfile
import datetime
import json
from PySide6.QtWidgets import QStyle, QFileDialog, QMessageBox
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from manager.media_manager import MediaManager
from gui.center_panel.video_item import VideoItem, BackgroundItem
from engine.caption.caption_flow import apply_caption, get_transcript_data
from gui.utils.bg_service import BackgroundService
from engine.render_engine import RenderWorker
from gui.right_panel.caption_tab import CaptionTab
from gui.center_panel.caption_item import CaptionItem
from engine.caption import word_adapter

# [BARU] Import Text Effect & ASS Builder
from engine.effects.text_advanced import TextAdvancedEffect
from engine.caption.ass_builder import make_ass_from_words

# Nama file config
CONFIG_FILE = "user_config.json"

class EditorController:
    def __init__(self, main_window):
        self.view = main_window
        self.preview = main_window.preview
        self.layer_panel = main_window.layer_panel
        self.setting = main_window.setting
        self.engine = main_window.engine
        
        self.bg_item = None
        self.audio_tracks = []
        self.worker = None
        self.temp_files = [] # Init temp files list
        self.transcript_data = [] # Menyimpan data asli transkrip
        self.caption_style = {}   # Menyimpan settingan style
        self.project_loaded = True 

        self._connect_signals()
        # Load Config saat aplikasi mulai
        self.load_app_config()
        self.validate_render_state()

        # [BARU] Setup koneksi Caption Tab
        if hasattr(self.setting, 'caption_tab'):
            self.setting.caption_tab.sig_enable_toggled.connect(self.on_caption_enabled)
            self.setting.caption_tab.sig_style_changed.connect(self.on_caption_style_changed)
            self.setting.caption_tab.sig_generate_caption.connect(self.on_generate_caption)
            
    # --- [BAGIAN CONFIG: SAVE & LOAD] ---
    def load_app_config(self):
        """Membaca file JSON dan mengisi folder output terakhir"""
        default_path = os.path.join(os.path.expanduser("~"), "Videos")
        if not os.path.exists(default_path):
             default_path = os.path.expanduser("~")
             
        final_path = default_path

        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    saved_path = data.get("last_output_folder", "")
                    if saved_path and os.path.exists(saved_path):
                        final_path = saved_path
                        print(f"[CONFIG] Loaded last path: {final_path}")
        except Exception as e:
            print(f"[CONFIG] Error loading config: {e}")

        # Set ke UI
        self.layer_panel.render_tab.txt_folder.setText(final_path)

    def save_app_config(self):
        """Menyimpan folder output saat ini ke JSON"""
        current_folder = self.layer_panel.render_tab.txt_folder.text().strip()
        
        data = {
            "last_output_folder": current_folder
        }
        
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=4)
            print("[CONFIG] Settings saved successfully.")
        except Exception as e:
            print(f"[CONFIG] Error saving config: {e}")
            
    def _connect_signals(self):
        self.layer_panel.sig_layer_selected.connect(self.preview.select_frame_by_code)
        self.layer_panel.sig_layer_selected.connect(self.on_canvas_selection_update_ui)
        self.preview.sig_frame_selected.connect(self.layer_panel.select_layer_by_code)
        self.layer_panel.sig_layer_created.connect(self.on_create_visual_item)
        self.layer_panel.sig_layer_reordered.connect(self.on_layer_reordered)
        self.layer_panel.sig_delete_layer.connect(self.on_layer_deleted)

        self.setting.on_setting_change.connect(self.on_setting_changed)
        self.preview.scene.selectionChanged.connect(self.on_canvas_selection_update_ui)
        self.preview.sig_item_moved.connect(self.on_canvas_item_moved)

        self.layer_panel.btn_add_bg.clicked.connect(self.on_add_bg_clicked)
        self.layer_panel.sig_bg_changed.connect(self.on_bg_properties_changed)
        self.layer_panel.sig_bg_toggle.connect(self.on_bg_toggle_changed)
        self.layer_panel.btn_add_content.clicked.connect(self.on_add_content_clicked)
        
        # --- [FIX] TEXT BUTTON CONNECTIONS ---
        # 1. Dari Layer Panel (Tombol Lama)
        if hasattr(self.layer_panel, 'btn_add_text'):
            self.layer_panel.btn_add_text.clicked.connect(self.add_text_simple)
        if hasattr(self.layer_panel, 'btn_add_paragraph'):
            self.layer_panel.btn_add_paragraph.clicked.connect(self.add_text_paragraph)
            
        # 2. Dari Preview Panel (Tombol Baru)
        if hasattr(self.preview, 'btn_add_text'):
            self.preview.btn_add_text.clicked.connect(self.add_text_simple)
        if hasattr(self.preview, 'btn_add_para'):
            self.preview.btn_add_para.clicked.connect(self.add_text_paragraph)

        if hasattr(self.layer_panel, 'tab_audio'):
            self.layer_panel.tab_audio.music_list.btn_import.clicked.connect(self.on_import_audio_clicked)
            self.layer_panel.tab_audio.sfx_list.btn_import.clicked.connect(self.on_import_audio_clicked)
            self.layer_panel.btn_add_audio.clicked.connect(self.on_add_audio_clicked)

        if hasattr(self.layer_panel, 'tab_templates'):
            self.layer_panel.tab_templates.sig_load_template.connect(self.on_template_loaded)
        if hasattr(self.layer_panel, 'tab_chroma'):
            self.layer_panel.tab_chroma.btn_save_selection.clicked.connect(self.on_save_chroma_preset)
        
        self.preview.timeline_slider.valueChanged.connect(self.on_slider_moved)
        self.preview.btn_play.clicked.connect(self.engine.toggle_play)
        self.engine.sig_time_changed.connect(self.update_ui_from_engine)
        self.engine.sig_state_changed.connect(self.update_play_button_icon)
        
        self.setting.sig_bulk_requested.connect(self.on_bulk_process_start)
        # Update list layer di Bulk Tab setiap kali ada layer dibuat/dihapus
        self.layer_panel.sig_layer_created.connect(self.update_bulk_tab_layers)
        self.layer_panel.sig_delete_layer.connect(self.update_bulk_tab_layers)
        
        # --- CONNECT RENDER TAB BUTTONS ---
        self.layer_panel.render_tab.btn_render.clicked.connect(self.on_render_clicked)
        self.layer_panel.render_tab.btn_stop.clicked.connect(self.on_stop_render_clicked)
        # Connect tombol Buka Folder
        self.layer_panel.render_tab.btn_open_folder.clicked.connect(self.on_open_folder_clicked)
        
        self.preview.sig_item_moved.connect(self.on_visual_item_moved)
        self.layer_panel.sig_layer_created.connect(self.validate_render_state)
        self.layer_panel.sig_delete_layer.connect(self.validate_render_state)
        self.layer_panel.sig_bg_toggle.connect(self.validate_render_state)
        self.layer_panel.sig_bg_changed.connect(self.validate_render_state) 
        self.layer_panel.btn_add_bg.clicked.connect(lambda: QTimer.singleShot(500, self.validate_render_state))
        
        if hasattr(self.preview.view, 'sig_dropped'):
            self.preview.view.sig_dropped.connect(self.validate_render_state)


    def on_visual_item_moved(self, data):
        """Dipanggil saat item di canvas bergerak (drag mouse)"""
        if hasattr(self, 'setting'):
            self.setting.set_values(data)
            
        if data.get("is_bg", False) and self.layer_panel:
            self.layer_panel.set_bg_values(data)
            
    def connect_bg_signals(self, bg_item):
        self.bg_item = bg_item
        self.bg_item.signals.sig_moved.connect(self.on_visual_item_moved)
        
    def recalculate_global_duration(self):
        max_end = 5.0
        for item in self.preview.scene.items():
            if isinstance(item, VideoItem):
                end_t = getattr(item, 'end_time', None)
                if end_t is not None and end_t > max_end:
                    max_end = end_t
        
        self.engine.set_duration(max_end)
        self.preview.timeline_slider.blockSignals(True)
        self.preview.timeline_slider.setRange(0, int(max_end * 100))
        self.preview.timeline_slider.blockSignals(False)
        print(f"[AUTO-DURATION] Global Duration Updated to: {max_end:.2f}s")
        
    def on_setting_changed(self, data):
        selected = self.preview.scene.selectedItems()
        if not selected or not isinstance(selected[0], VideoItem): return
        item = selected[0]
        is_bg = isinstance(item, BackgroundItem)

        if "start_time" in data or "end_time" in data:
            new_start = float(data.get("start_time", item.start_time))
            new_end = float(data.get("end_time", item.end_time if item.end_time is not None else new_start + 5.0))
            new_dur = max(0.1, new_end - new_start)
            item.set_time_range(new_start, new_dur)
            self.recalculate_global_duration()
            item.apply_global_time(self.engine.current_time)
        
        filtered_data = {k: v for k, v in data.items() if k not in ['start_time', 'end_time']}
        item.settings.update(filtered_data)

        if is_bg:
            item.update_bg_settings(filtered_data)
        else:
            if data.get("type") == "text":
                if "rotation" in data: item.setRotation(data["rotation"])
                item.refresh_text_render()
            else:
                if "x" in data and "y" in data: item.setPos(data["x"], data["y"])
                if "frame_rot" in data: item.setRotation(data["frame_rot"])
                if "frame_w" in data and "frame_h" in data:
                    item.setRect(0, 0, data["frame_w"], data["frame_h"])
                    item.setTransformOriginPoint(item.rect().center())
            item.update() 
            
        self.layer_panel.set_delete_enabled(not item.settings.get("lock"))

    def on_canvas_selection_update_ui(self):
        try:
            if not self.preview or not self.preview.scene: return
            
            selected = self.preview.scene.selectedItems()
            enable_content_buttons = False
            
            if selected and isinstance(selected[0], VideoItem):
                item = selected[0]
                is_bg = isinstance(item, BackgroundItem)
                enable_content_buttons = not is_bg
                
                # Data dasar
                data_to_update = {
                    "frame_rot": int(item.rotation()),
                    "frame_w": int(item.rect().width()),
                    "frame_h": int(item.rect().height()),
                    "start_time": item.start_time,
                    "end_time": item.end_time
                }

                if not is_bg:
                    data_to_update["x"] = int(item.pos().x())
                    data_to_update["y"] = int(item.pos().y())
                
                item.settings.update(data_to_update)
                
                self.setting.set_values(item.settings)
                self.setting.set_active_tab_by_type(item.settings.get("content_type", "media"))
                
                is_locked = item.settings.get("lock", False)
                self.layer_panel.set_delete_enabled(not is_locked and not is_bg)
                self.layer_panel.set_reorder_enabled(not is_locked and not is_bg)
            else:
                self.layer_panel.set_delete_enabled(False)
                self.layer_panel.set_reorder_enabled(False)
                
            if hasattr(self.layer_panel, 'set_content_button_enabled'):
                self.layer_panel.set_content_button_enabled(enable_content_buttons)
        except RuntimeError:
            pass

    def on_create_visual_item(self, frame_code, shape="portrait"):
        if frame_code == "CAPTION_LAYER": return
        self.layer_panel.add_layer_item_custom(f"FRAME {frame_code}")
        item = VideoItem(frame_code, None, None, shape=shape)
        self.preview.scene.addItem(item)
        item.setPos(50, 50)
        item.settings.update({'x': 50, 'y': 50})
        self.preview.scene.clearSelection()
        item.setSelected(True)
        self.recalculate_global_duration()
        self.validate_render_state()

    # --- [NEW] TEXT LOGIC ---
    def add_text_simple(self):
        """Menambahkan Teks Biasa (Auto Width, No Wrap)"""
        self._create_text_item(default_text="Simple Text", is_paragraph=False)

    def add_text_paragraph(self):
        """Menambahkan Paragraf (Fixed Box Width, Auto Wrap)"""
        self._create_text_item(default_text="Insert paragraph here...", is_paragraph=True)
    
    def _create_text_item(self, default_text, is_paragraph):
        # 1. Add to Layer Panel
        count = 0
        if hasattr(self.layer_panel, 'list_layers'):
             count = self.layer_panel.list_layers.count()
        suffix = f"{'PARA' if is_paragraph else 'TXT'} {count}"
        
        # Add visual entry in Layer Panel
        self.layer_panel.add_layer_item_custom(f"FRAME {suffix}")
        
        # 2. Create VideoItem (Shape Text)
        item = VideoItem(suffix, None, None, shape="text") 
        self.preview.scene.addItem(item)
        item.setPos(100, 100)
        
        # 3. Setup Settings (BOX WIDTH is Key)
        # 0 = Auto/Text, >0 = Paragraph
        box_w = 800 if is_paragraph else 0
        font_s = 60 if is_paragraph else 80
        align = "left" if is_paragraph else "center"
        
        item.settings.update({
            "text_content": default_text,
            "content_type": "text",
            "box_width": box_w,
            "font_size": font_s,
            "alignment": align,
            "x": 100,
            "y": 100
        })
        
        # 4. Update UI
        self.preview.scene.clearSelection()
        item.setSelected(True)
        self.setting.set_values(item.settings)
        self.recalculate_global_duration()
        self.validate_render_state()
        return item 

    def update_text_item(self, data):
        item = self.get_selected_item()
        if isinstance(item, CaptionItem): # Jika class UI nya beda
            pass # Handle caption
        elif hasattr(item, 'refresh_text_render'):
            # General text update logic
            item.settings.update(data)
            item.refresh_text_render()
            item.update()
        
    def on_open_folder_clicked(self):
        folder_path = self.layer_panel.render_tab.txt_folder.text().strip()
        if folder_path and os.path.exists(folder_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder_path))
        else:
            QMessageBox.warning(self.view, "Folder Tidak Ditemukan", "Folder tujuan belum dipilih atau tidak ada.")
         
    # --- [NEW] CAPTION LOGIC STARTS HERE ---
    def on_caption_enabled(self, enabled):
        scene = self.preview.scene
        caption_item = next((i for i in scene.items() if getattr(i, 'name', '') == "CAPTION_LAYER"), None)
        
        if enabled:
            if not caption_item:
                caption_item = CaptionItem("CAPTION_LAYER")
                caption_item.settings.update({
                    "text_content": "MAMEN CAPTION",
                    "font": "Arial", "font_size": 42, "text_color": "#ffffff",
                    "stroke_on": True, "stroke_width": 3, "stroke_color": "#000000",
                    "bg_on": False
                })
                scene.addItem(caption_item)
                caption_item.setZValue(9999) 
                
                scene_w = self.preview.scene.sceneRect().width()
                caption_item.refresh_text_render() 
                item_w = caption_item.rect().width()
                start_x = (scene_w - item_w) / 2
                caption_item.setPos(start_x, 800) 
                caption_item.settings['x'] = start_x
                
            caption_item.setVisible(True)
            caption_item.setSelected(True)
        else:
            if caption_item: caption_item.setVisible(False)
                
    def on_caption_style_changed(self, data):
        caption_item = next((i for i in self.preview.scene.items() if getattr(i, 'name', '') == "CAPTION_LAYER"), None)
        if caption_item:
            caption_item.settings.update(data)
            caption_item.refresh_text_render()
            if "alignment" in data or "margin_v" in data:
                self._apply_caption_alignment(caption_item)

    def _apply_caption_alignment(self, item):
        s = item.settings
        alignment = s.get("alignment", "bottom_center")
        margin_v = s.get("margin_v", 40)
        margin_h = s.get("margin_h", 60)
        
        scene_rect = self.preview.scene.sceneRect()
        W, H = scene_rect.width(), scene_rect.height()
        w, h = item.rect().width(), item.rect().height()
        
        new_y = H - h - margin_v
        if alignment == "middle_lower": new_y = (H * 0.75) - (h / 2)

        if alignment == "bottom_left": new_x = margin_h
        elif alignment == "bottom_right": new_x = W - w - margin_h
        else: new_x = (W - w) / 2
            
        item.setPos(new_x, new_y)
        item.settings['x'] = new_x
        item.settings['y'] = new_y
            
    def on_generate_caption(self, data):
        api_key = data.get("api_key")
        if not api_key:
            print("[ERROR] API Key Missing")
            return
        import threading
        t = threading.Thread(target=self._process_transcription, args=(api_key, data))
        t.start()
        
    def _get_api_key_from_env(self):
        env_path = os.path.join(os.getcwd(), "engine", "caption", ".env")
        if not os.path.exists(env_path): return None
        try:
            with open(env_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ASSEMBLYAI_API_KEY="): return line.split("=", 1)[1]
        except Exception as e:
            print(f"[ERROR] Gagal baca .env: {e}")
        return None

    def _run_transcription_process(self, config):
        try:
            from engine.caption import assemblyai, word_adapter, ass_builder
            audio_source = getattr(self, 'current_audio_path', None)
            if not audio_source and self.audio_tracks: audio_source = self.audio_tracks[0]

            if not audio_source: 
                print("[CAPTION] No audio source found.")
                return None

            print(f"[CAPTION] Start Transcribing: {audio_source}")
            words = assemblyai.transcribe_audio(config["api_key"], audio_source)
            if not words: return None
                
            grouped_lines = word_adapter.group_words_custom(words, config)
            ass_data = word_adapter.format_lines_to_ass(grouped_lines)
            ass_path = ass_builder.build_ass_file(ass_data, config) 
            
            print(f"[CAPTION] ASS File created: {ass_path}")
            return ass_path

        except Exception as e:
            print(f"[ERROR TRANSCRIPTION] {e}")
            import traceback; traceback.print_exc()
            return None

    def _process_transcription(self, api_key, config):
        from engine.caption import assemblyai
        audio_path = self.current_audio_path 
        words = assemblyai.transcribe_audio(api_key, audio_path)
        if not words:
            print("[ERROR] Transcription failed or empty")
            return
        grouped_lines = word_adapter.group_words_custom(words, config)
        ass_data = word_adapter.format_lines_to_ass(grouped_lines)
        from engine.caption import ass_builder
        ass_content = ass_builder.build_ass_file(ass_data)
        
    # --- RENDER LOGIC ---
    def on_render_clicked(self):
        self.save_app_config()
        self.recalculate_global_duration()
        current_duration = max(0.1, float(self.engine.duration))
        
        folder_path = self.layer_panel.render_tab.txt_folder.text().strip()
        if not folder_path or not os.path.exists(folder_path):
            QMessageBox.warning(self.view, "Folder Tidak Valid", "Silakan pilih folder tujuan yang valid!")
            return
            
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"MamenPro_{timestamp}.mp4"
        output_path = os.path.join(folder_path, filename)
        
        quality_txt = self.layer_panel.render_tab.combo_quality.currentText()
        if "480p" in quality_txt: target_short = 480
        elif "720p" in quality_txt: target_short = 720
        elif "4K" in quality_txt: target_short = 2160
        else: target_short = 1080
        
        scene_rect = self.preview.scene.sceneRect()
        orig_w, orig_h = scene_rect.width(), scene_rect.height()
        is_landscape = orig_w >= orig_h
        current_short = orig_h if is_landscape else orig_w
        scale_factor = target_short / current_short
        
        final_w, final_h = int(orig_w * scale_factor), int(orig_h * scale_factor)
        if final_w % 2 != 0: final_w += 1
        if final_h % 2 != 0: final_h += 1
        
        caption_ass_path = None
        is_caption_on = False
        if hasattr(self.setting, 'caption_tab'):
             is_caption_on = self.setting.caption_tab.chk_enable_caption.isChecked()

        if is_caption_on:
            self.layer_panel.render_tab.btn_render.setText("Transcribing...")
            self.view.repaint() 

            api_key = self._get_api_key_from_env()
            if not api_key:
                QMessageBox.critical(self.view, "API Key Missing", "API Key AssemblyAI tidak ditemukan di engine/caption/.env")
                self.layer_panel.render_tab.btn_render.setText("MULAI RENDER")
                return

            caption_layer = next((i for i in self.preview.scene.items() if getattr(i, 'name', '') == "CAPTION_LAYER"), None)
            
            caption_config = {"api_key": api_key, "max_chars": 30, "max_lines": 2}
            if caption_layer:
                caption_config.update(caption_layer.settings)
                canvas_h = self.preview.scene.sceneRect().height()
                item_bottom_y = caption_layer.y() + caption_layer.rect().height()
                margin_v = int(canvas_h - item_bottom_y)
                caption_config['margin_v'] = max(10, margin_v)

            caption_ass_path = self._run_transcription_process(caption_config)
            if caption_ass_path: self.temp_files.append(caption_ass_path)
            else: print("[WARN] Caption generation failed.")

        items_data = []
        active_items = [i for i in self.preview.scene.items() if isinstance(i, VideoItem)]

        for item in active_items:
            if getattr(item, 'name', '') == "CAPTION_LAYER": continue
            if item.opacity() == 0 or not item.isVisible(): continue
            is_bg = isinstance(item, BackgroundItem)
            is_text = item.settings.get("content_type") == "text"
            render_path = None
            is_static_image = False 

            if is_text:
                if not item.current_pixmap: item.refresh_text_render()
                if item.current_pixmap:
                    fd, temp_path = tempfile.mkstemp(suffix=".png")
                    os.close(fd)
                    item.current_pixmap.save(temp_path, "PNG")
                    self.temp_files.append(temp_path)
                    render_path = temp_path
                    is_static_image = True 
            else:
                render_path = item.file_path
            
            if not render_path: continue
            if not is_text:
                ext = os.path.splitext(render_path)[1].lower()
                if ext in ['.png', '.jpg', '.jpeg', '.webp', '.bmp']: is_static_image = True
                else: is_static_image = False 

            if is_bg:
                if not item.current_pixmap: continue
                canvas_w, canvas_h = int(orig_w), int(orig_h)
                pix_w, pix_h = item.current_pixmap.width(), item.current_pixmap.height()
                scale_w, scale_h = canvas_w / pix_w, canvas_h / pix_h
                base_scale = max(scale_w, scale_h)
                user_scale = item.settings.get('scale', 100) / 100.0
                final_scale = base_scale * user_scale
                vw, vh = int(pix_w * final_scale), int(pix_h * final_scale)
                px = int((canvas_w / 2) - (vw / 2) + item.settings.get('x', 0))
                py = int((canvas_h / 2) - (vh / 2) + item.settings.get('y', 0))
            else:
                vw, vh = int(item.rect().width()), int(item.rect().height())
                px, py = int(item.x()), int(item.y())
                
            items_data.append({
                'path': render_path, 'is_image': is_static_image,
                'x': px, 'y': py, 'visual_w': vw, 'visual_h': vh,
                'rot': int(item.rotation()), 
                'opacity': item.settings.get('opacity', 100),
                'z_value': item.zValue(),
                'start_time': item.start_time, 'end_time': item.end_time,
                'sf_l': item.settings.get('sf_l', 0), 'sf_r': item.settings.get('sf_r', 0),
                'f_l': item.settings.get('f_l', 0), 'f_r': item.settings.get('f_r', 0),
                'f_t': item.settings.get('f_t', 0), 'f_b': item.settings.get('f_b', 0),
                'blur': item.settings.get('blur', 0),
                'vig_strength': item.settings.get('vig_strength', 0.0),
                'vig_angle': item.settings.get('vig_angle', 0.0)
            })

        ratio_mult = scale_factor 
        for it in items_data:
            it['x'] = int(it['x'] * ratio_mult)
            it['y'] = int(it['y'] * ratio_mult)
            it['visual_w'] = int(it['visual_w'] * ratio_mult)
            it['visual_h'] = int(it['visual_h'] * ratio_mult)

        self.layer_panel.render_tab.btn_render.setEnabled(False)
        self.layer_panel.render_tab.btn_stop.setEnabled(True)
        self.layer_panel.render_tab.btn_render.setText("Rendering...")
        
        self.worker = RenderWorker(
            items_data, output_path, current_duration, 
            final_w, final_h, self.audio_tracks,
            subtitle_file=caption_ass_path 
        )
        self.worker.sig_finished.connect(self.on_render_finished)
        self.worker.start()

    def on_render_finished(self, success, msg):
        self.layer_panel.render_tab.btn_render.setEnabled(True)
        self.layer_panel.render_tab.btn_stop.setEnabled(False)
        self.layer_panel.render_tab.btn_render.setText("🎬 MULAI RENDER")
        for f in self.temp_files:
            try: os.remove(f)
            except: pass
        self.temp_files.clear()
        if success: 
            reply = QMessageBox.question(self.view, "Sukses", f"{msg}\n\nBuka folder output?", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes: self.on_open_folder_clicked()
        else: QMessageBox.warning(self.view, "Render Gagal/Stop", msg)

    def on_stop_render_clicked(self):
        if self.worker and self.worker.isRunning():
            self.layer_panel.render_tab.btn_stop.setText("Stopping...")
            self.layer_panel.render_tab.btn_stop.setEnabled(False)
            self.worker.stop()
            
    def validate_render_state(self, *args):
        has_bg = self.layer_panel.chk_bg_toggle.isChecked() and (self.bg_item is not None)
        has_clips = any(isinstance(item, VideoItem) and not isinstance(item, BackgroundItem) 
                       for item in self.preview.scene.items())
        can_render = has_bg or has_clips
        btn = self.layer_panel.render_tab.btn_render
        btn.setEnabled(can_render)
        if can_render:
            btn.setStyleSheet("background-color: #2a9d8f; color: white; font-size: 14px; font-weight: bold;")
            btn.setToolTip("Siap Render")
        else:
            btn.setStyleSheet("background-color: #555555; color: #aaaaaa; font-size: 14px; font-weight: bold;")
            btn.setToolTip("Scene kosong! Tambahkan BG atau Clip.")
   
    def on_render_progress(self, msg): print(f"[RENDER] {msg}")
    
    def on_add_bg_clicked(self):
        if not self.layer_panel.chk_bg_toggle.isChecked(): return
        data = MediaManager.open_media_dialog(self.view, "Pilih Background")
        if not data: return
        if self.bg_item: self.preview.scene.removeItem(self.bg_item)
        self.bg_item = BackgroundItem(data['path'], self.preview.scene.sceneRect())
        self.connect_bg_signals(self.bg_item) 
        self.bg_item.seek_to(0)
        self.bg_item.update_bg_settings({'x': 0, 'y': 0, 'scale': 100, 'fit': 'cover', 'blur': 0, 'vig': 0})
        self.layer_panel.set_bg_values(self.bg_item.settings)
        self.layer_panel.show_bg_controls(True)
        self.preview.scene.addItem(self.bg_item)
        self.recalculate_global_duration()
        self.validate_render_state()

    def on_add_content_clicked(self):
        selected = self.preview.scene.selectedItems()
        if not selected or not isinstance(selected[0], VideoItem): return
        data = MediaManager.open_media_dialog(self.view)
        if data:
            selected[0].set_content(data['path'])
            self.setting.set_values(selected[0].settings)
            self.recalculate_global_duration()
            self.preview.scene.update()
            
    def on_layer_reordered(self):
        lw = self.layer_panel.list_layers
        for i in range(lw.count()):
            name = lw.item(i).text().replace("FRAME ", "")
            for g_item in self.preview.scene.items():
                if hasattr(g_item, 'name') and g_item.name == name:
                    g_item.setZValue(lw.count() - i)
                    break
        self.preview.scene.update()

    def on_layer_deleted(self, frame_code):
        for item in self.preview.scene.items():
            if hasattr(item, 'name') and item.name == frame_code:
                self.preview.scene.removeItem(item)
                break
        self.recalculate_global_duration()
        self.validate_render_state()

    def update_ui_from_engine(self, t):
        self.preview.timeline_slider.blockSignals(True)
        self.preview.timeline_slider.setValue(int(t * 100))
        self.preview.timeline_slider.blockSignals(False)
        for item in self.preview.scene.items():
            if hasattr(item, 'apply_global_time'): item.apply_global_time(t)
            elif hasattr(item, 'seek_to'): item.seek_to(t)

    def on_slider_moved(self, value):
        if not self.engine.timer.isActive():
            self.engine.set_time(value / 100.0)

    def update_play_button_icon(self, is_playing):
        icon = QStyle.SP_MediaPause if is_playing else QStyle.SP_MediaPlay
        self.preview.btn_play.setIcon(self.view.style().standardIcon(icon))

    def on_bg_properties_changed(self, data):
        if self.bg_item: self.bg_item.update_bg_settings(data)
    def on_bg_toggle_changed(self, is_on):
        if self.bg_item: self.bg_item.setVisible(is_on)
    def on_import_audio_clicked(self):
        data = MediaManager.open_audio_dialog(self.view)
        if data: self.audio_tracks.append(data['path'])
    def on_add_audio_clicked(self): self.on_import_audio_clicked()
    def on_canvas_item_moved(self, data): self.setting.set_values(data)
    def on_template_loaded(self, data): pass
    def on_save_chroma_preset(self): pass
    
    def update_bulk_tab_layers(self, *args):
        text_layers = []
        for item in self.preview.scene.items():
            if hasattr(item, 'settings') and item.settings.get("content_type") == "text":
                text_layers.append(item.name)
        self.setting.bulk_tab.update_layer_list(text_layers)

    def on_bulk_process_start(self, data):
        print(f"[BULK] Memulai proses untuk {len(data['raw_data'])} item.")
        print(f"[BULK] Target Layer: {data['target_layer']}")
        
    def _hex_to_ass_color(self, hex_color):
        if not hex_color: return "&H00FFFFFF"
        hex_color = hex_color.lstrip("#")
        if len(hex_color) == 6:
            r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]
            return f"&H00{b}{g}{r}"
        return "&H00FFFFFF"