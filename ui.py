# ui.py
from datetime import datetime
from PySide6.QtWidgets import (QMainWindow, QTabWidget, QTableWidget, QTableWidgetItem, QWidget, QLabel, QPushButton, QStatusBar, QVBoxLayout, QHBoxLayout, QCheckBox, QMessageBox)
from PySide6.QtGui import QBrush
from PySide6.QtCore import QTimer, Qt

from config import (
    TAB_SPECS, COL_STATUS, COL_SELECT, COL_MET, COL_PATIENT, COL_IPP, COL_TASK, COL_TASK_STATUS, COL_PHYSICIST, COL_NOTE, COL_TIMEPLANNER, COL_FOLDER, COL_DICOM, COL_PDF, COL_ADDRESS, COL_ENERGY, COL_DURATION, COL_LOCALISATION, COLUMN_COUNT,
    PASTEL_NONE, PASTEL_LATE, PASTEL_URGENT, PASTEL_OK, REFRESH_INTERVAL_MS, BLINK_INTERVAL_MS, MACHINE_ORDER,
    add_business_days, log, machine_display, DashboardData
)
from database import SqlWorkerThread
from network import NetworkScannerThread

class CollapsibleWidget(QWidget):
    def __init__(self, title="Titre"):
        super().__init__()
        self._title = title
        self.toggle_button = QPushButton(f"▶ {title}")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setStyleSheet("QPushButton { text-align: left; padding: 9px 12px; font-weight: 600; background-color: #EEF3EE; color: #355E3B; border: 1px solid #CFE3CF; border-radius: 8px; } QPushButton:hover { background-color: #E3EEE3; }")
        self.content = QLabel("")
        self.content.setVisible(False)
        self.content.setStyleSheet("QLabel { padding: 10px; background-color: #FFFFFF; border-left: 1px solid #CFE3CF; border-right: 1px solid #CFE3CF; border-bottom: 1px solid #CFE3CF; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content)
        self.toggle_button.clicked.connect(self.toggle)

    def set_title(self, title):
        self._title = title
        self.toggle_button.setText(f"{'▼' if self.toggle_button.isChecked() else '▶'} {title}")

    def toggle(self):
        self.content.setVisible(self.toggle_button.isChecked())
        self.set_title(self._title)

def create_centered_checkbox(checked=True):
    w = QWidget()
    l = QHBoxLayout(w)
    l.setContentsMargins(0,0,0,0)
    cb = QCheckBox()
    cb.setChecked(checked)
    l.addWidget(cb, alignment=Qt.AlignCenter)
    w.checkbox = cb
    return w

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CQ Dashboard")
        self.resize(1400, 500)
        
        self.now = datetime.now()
        self.limit = add_business_days(self.now, 2)
        self.tables_refs = {} # { "tomo2": QTableWidget, ... }
        self.db_error_shown = False
        self.db_alert_level = "unknown"
        self.blink_state = False

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 8)
        
        self.qa_label = QLabel()
        self.qa_label.setStyleSheet("QLabel { background-color: #FFF6DE; border: 1px solid #F5D98B; border-radius: 8px; padding: 10px 14px; font-weight: 600; color: #6B5215; }")
        layout.addWidget(self.qa_label)

        self.machine_label = QLabel()
        self.machine_label.setStyleSheet("QLabel { background-color: #E9F1FF; border: 1px solid #AECBF5; border-radius: 8px; padding: 10px 14px; font-weight: 600; color: #204E8C; }")
        layout.addWidget(self.machine_label)

        self.network_label = QLabel()
        self.network_label.setVisible(False)
        self.network_label.setStyleSheet("QLabel { background-color: #FDE7E7; border: 1px solid #F0A9A9; border-radius: 8px; padding: 8px 12px; font-weight: 600; color: #8C1F1F; }")
        layout.addWidget(self.network_label)

        self.qa_legend = QLabel("* : patients restants")
        self.qa_legend.setStyleSheet("QLabel { color: #9497A0; font-size: 11px; }")
        layout.addWidget(self.qa_legend)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self.on_tab_changed)
        layout.addWidget(self.tabs)
        self.setCentralWidget(root)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.last_refresh_label = QLabel("Dernier refresh : -")
        self.status_bar.addPermanentWidget(self.last_refresh_label)
        self.db_label = QLabel("DB : -")
        self.db_label.setMinimumWidth(320)
        self.status_bar.addPermanentWidget(self.db_label)

        # Threads
        self.sql_worker = SqlWorkerThread(self)
        self.sql_worker.data_loaded.connect(self.on_db_loaded)
        self.sql_worker.error_occurred.connect(self.on_error)

        self.net_worker = None

        # Timers
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.trigger_refresh)
        self.timer.start(REFRESH_INTERVAL_MS)
        
        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.toggle_db_blink)
        self.blink_timer.start(BLINK_INTERVAL_MS)

        self.trigger_refresh()

    def trigger_refresh(self):
        if self.sql_worker.isRunning(): return
        if self.net_worker and self.net_worker.isRunning():
            self.net_worker.cancel()
            self.net_worker.wait()
        
        self.last_refresh_label.setText("Actualisation SQL (instantané)...")
        self.network_label.setVisible(False)
        self.sql_worker.start()

    def on_db_loaded(self, data: DashboardData):
        self.now = datetime.now()
        self.limit = add_business_days(self.now, 2)
        if self.db_error_shown:
            QMessageBox.information(self, "Connexion rétablie", "SQL disponible.")
            self.db_error_shown = False

        self.update_qa_header(data.qa, data.countdown, data.machines)
        self.update_machine_footer(data.machine_schedule, data.remaining_today)

        current_idx = self.tabs.currentIndex()
        self.tabs.blockSignals(True)
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            w.setParent(None)
            w.deleteLater()
        
        self.tables_refs.clear()

        for tab_name, attr, waiting_key in TAB_SPECS:
            rows = getattr(data, attr)
            tab, table = self.create_table_tab(rows, tab_name, data.waiting_count.get(waiting_key, 0), data.waiting_details.get(waiting_key, []))
            self.tables_refs[attr] = table
            self.tabs.addTab(tab, f"{tab_name} ({len(rows)})")

        self.tabs.blockSignals(False)
        self.tabs.setCurrentIndex(max(0, current_idx))
        self.on_tab_changed(self.tabs.currentIndex())

        self.last_refresh_label.setText(f"Dernier refresh : {self.now.strftime('%H:%M:%S')} (Scan NAS en cours ⏳)")
        self.update_db_status(data.last_db_update)

        # START NETWORK SCAN
        self.net_worker = NetworkScannerThread(data, self)
        self.net_worker.row_updated.connect(self.on_network_row_updated)
        self.net_worker.scan_finished.connect(self.on_network_finished)
        self.net_worker.start()

    def on_network_row_updated(self, tab_id: str, row: int, patient: dict):
        table = self.tables_refs.get(tab_id)
        if not table or row >= table.rowCount(): return

        bg = table.item(row, COL_STATUS).background()
        
        def update_cell(col, text, tooltip=None):
            i = QTableWidgetItem(str(text))
            i.setBackground(bg)
            i.setTextAlignment(Qt.AlignCenter)
            if tooltip: i.setToolTip(tooltip)
            table.setItem(row, col, i)

        update_cell(COL_FOLDER, "✅" if patient.get("existing_folder") else "❌")
        update_cell(COL_DICOM, "✅" if patient.get("existing_dicom") else "❌")
        
        pdf_date = patient.get("pdf_date")
        update_cell(COL_PDF, "✅" if patient.get("existing_pdf") else "❌", f"PDF validé : {pdf_date.strftime('%d/%m/%Y %H:%M')}" if pdf_date else None)

        p = str(patient.get("folder_path") or "")
        adr = "Hyperarc" if "Hyperarc" in p else "EC" if "EC_IUC" in p else "RA" if "Patients_RA" in p else ""
        update_cell(COL_ADDRESS, adr)
        
        update_cell(COL_ENERGY, patient.get("energy") or "")
        dur = patient.get("tomo_duration_min")
        update_cell(COL_DURATION, dur if dur is not None else "")
        frac = patient.get("tomo_fraction")
        update_cell(COL_LOCALISATION, frac if frac is not None else "")

    def on_network_finished(self, errors: list):
        self.last_refresh_label.setText(f"Dernier refresh : {self.now.strftime('%H:%M:%S')} (NAS terminé ✅)")
        if errors:
            self.network_label.setText("⚠️ NAS partiellement injoignable.<br><span style='font-weight:400;'>" + "<br>".join(f"• {e}" for e in errors[:3]) + "</span>")
            self.network_label.setVisible(True)

    def on_error(self, err_msg):
        self.last_refresh_label.setText(f"Échec : {datetime.now().strftime('%H:%M:%S')}")
        if not self.db_error_shown:
            QMessageBox.critical(self, "Erreur SQL", f"Connexion impossible:\n{err_msg}")
            self.db_error_shown = True

    def create_table_tab(self, data: list, tab_name: str, wait_count: int, wait_details: list):
        w = QWidget()
        l = QVBoxLayout(w)
        table = QTableWidget()
        table.setColumnCount(COLUMN_COUNT)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setHorizontalHeaderLabels(["Status","Select","MET Date","Patient","IPP","Task","Task Status","Physicien","Note","Time Planner","Folder","Dicom","PDF","Adress","Energy","Duration","Localisation"])
        
        footer = QLabel()
        footer.setStyleSheet("QLabel { background-color: #F3F4F7; padding: 8px 10px; font-weight: 600; border-top: 1px solid #E1E3E8; }")
        
        pw = CollapsibleWidget(f"Patients en cours de préparation : {tab_name.split('(')[0]} = {wait_count}")
        if wait_details:
            html = "<table cellspacing='6'><tr><th align='left'>IPP</th><th align='left'>Patient</th><th align='left'>TDM</th><th align='left'>Valid. Méd.</th><th align='left'>MET</th></tr>"
            for p in wait_details:
                html += f"<tr style='color:{'red' if p.get('va_tomber') else 'black'};'><td>{p['ipp']}</td><td>{p['last_name']} {p['first_name']}</td><td>{p.get('tdm_date').strftime('%d/%m') if p.get('tdm_date') else '-'}</td><td>{p.get('validation_date').strftime('%d/%m') if p.get('validation_date') else '-'}</td><td>{p.get('MET').strftime('%d/%m') if p.get('MET') else '-'}</td></tr>"
            pw.content.setText(html + "</table>")
        else: pw.content.setText("Aucun patient")

        table.setRowCount(len(data))
        for r, patient in enumerate(data):
            met = patient.get("met_start")
            if not met: c, dot = PASTEL_NONE, "⚪"
            elif met < self.now: c, dot = PASTEL_LATE, "🔴"
            elif met <= self.limit: c, dot = PASTEL_URGENT, "🟠"
            else: c, dot = PASTEL_OK, "🟢"

            table.setItem(r, COL_STATUS, QTableWidgetItem(dot))
            cb = create_centered_checkbox(True)
            cb.checkbox.stateChanged.connect(lambda: self.on_tab_changed(self.tabs.currentIndex()))
            table.setCellWidget(r, COL_SELECT, cb)
            table.setItem(r, COL_MET, QTableWidgetItem(met.strftime("%Y-%m-%d %H:%M") if met else ""))
            table.setItem(r, COL_PATIENT, QTableWidgetItem(f"{patient['last_name']} {patient['first_name']}"))
            table.setItem(r, COL_IPP, QTableWidgetItem(str(patient["ipp"])))
            table.setItem(r, COL_TASK, QTableWidgetItem(str(patient.get("task_display_focus", ""))))
            table.setItem(r, COL_TASK_STATUS, QTableWidgetItem(str(patient.get("task_status", ""))))
            table.setItem(r, COL_PHYSICIST, QTableWidgetItem(str(patient.get("physicist") or "")))
            
            note = QTableWidgetItem(str(patient.get("task_note") or ""))
            note.setTextAlignment(Qt.AlignCenter)
            table.setItem(r, COL_NOTE, note)
            
            tp = QTableWidgetItem("✅" if patient.get("Timeplanner") else "❌")
            tp.setTextAlignment(Qt.AlignCenter)
            table.setItem(r, COL_TIMEPLANNER, tp)

            # NETWORK PENDING (⏳)
            for col in [COL_FOLDER, COL_DICOM, COL_PDF, COL_ADDRESS, COL_ENERGY, COL_DURATION, COL_LOCALISATION]:
                i = QTableWidgetItem("⏳")
                i.setTextAlignment(Qt.AlignCenter)
                table.setItem(r, col, i)

            for col in range(COLUMN_COUNT):
                it = table.item(r, col)
                if it: it.setBackground(QBrush(c))

        table.resizeColumnsToContents()
        l.addWidget(table)
        l.addWidget(footer)
        l.addWidget(pw)
        w.footer, w.table, w.tab_name = footer, table, tab_name
        return w, table

    def on_tab_changed(self, idx):
        if idx < 0: return
        w = self.tabs.widget(idx)
        if not hasattr(w, "table"): return
        mins = 0
        for r in range(w.table.rowCount()):
            cw = w.table.cellWidget(r, COL_SELECT)
            if not cw or not cw.checkbox.isChecked(): continue
            txt = (w.table.item(r, COL_TASK).text() or "").lower()
            if "tomo" in txt:
                mins += 7 if mins == 0 else 0
                b = w.table.item(r, COL_DURATION).text()
                mins += 3 + (float(b) if b and b != "⏳" else 0)
            elif "ruby" in txt: mins += (8 if mins == 0 else 0) + 12
            elif "octa" in txt: mins += (11 if mins == 0 else 0) + 9
            else: mins += 15
        w.footer.setText(f"Temps estimé suivant sélection : {round(mins)} min")

    def update_qa_header(self, qa, countdown, machines):
        if not qa: self.qa_label.setText("Aucun créneau CQ"); return
        lines = []
        for q in qa:
            lines.append(f"{q['met_start'].strftime('%H:%M')} - {q.get('machine_label')}")
        self.qa_label.setText("Prochains créneaux CQ :   |   ".join(lines))

    def update_machine_footer(self, sched, remain):
        if not sched: self.machine_label.setText("Fin de journée : aucune donnée"); return
        sched.sort(key=lambda x: (MACHINE_ORDER.get(x.get("machine_key"), 99), x.get("start") or datetime.max))
        lines = [f"{machine_display(r.get('machine_key'))}: {r.get('start').strftime('%H:%M')}" for r in sched if r.get('start')]
        self.machine_label.setText("Fin de journée :   |   ".join(lines))

    def update_db_status(self, last):
        if not last: self.db_label.setText("⚪ DB Inconnue"); return
        dm = (self.now - last).total_seconds() / 60
        s = last.strftime('%H:%M:%S')
        self.db_alert_level = "critical" if dm > 15 else "alert" if dm > 10 else "warning" if dm > 5 else "ok"
        self.db_label.setText(f"{'🔴' if dm > 10 else '🟠' if dm > 5 else '🟢'} DB : {s}")
        self.db_label.setStyleSheet(f"QLabel {{ color: {'red' if dm > 10 else 'orange' if dm > 5 else 'green'}; font-weight: bold; }}")

    def toggle_db_blink(self):
        if self.db_alert_level != "critical": return
        self.blink_state = not self.blink_state
        self.db_label.setStyleSheet(f"QLabel {{ color: {'white' if self.blink_state else 'red'}; background-color: {'red' if self.blink_state else '#2b2b2b'}; font-weight: bold; padding: 0 5px; }}")

    def closeEvent(self, ev):
        self.timer.stop()
        if self.sql_worker.isRunning(): self.sql_worker.wait(2000)
        if self.net_worker and self.net_worker.isRunning():
            self.net_worker.cancel()
            self.net_worker.wait(2000)
        super().closeEvent(ev)