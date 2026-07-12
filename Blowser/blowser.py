#!/usr/bin/env python3
"""
BLOWSER - Firefox tarzı masaüstü web tarayıcısı
PyQt5 + QtWebEngine. Sekmeler üstte, tek araç çubuğu, yer imleri çubuğu,
geçmiş, indirmeler ve sayfa içi arama ile klasik bir Firefox deneyimi hedefler.
"""
import sys
import os
import sqlite3
from datetime import datetime

from PyQt5.QtCore import Qt, QUrl, QSettings, QSize
from PyQt5.QtGui import QKeySequence, QIcon
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QToolButton, QTabWidget, QTabBar, QLabel, QMenu,
    QDialog, QListWidget, QListWidgetItem, QMessageBox, QComboBox, QShortcut,
    QProgressBar, QFileDialog, QSizePolicy, QScrollArea, QFrame
)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineProfile, QWebEnginePage
from PyQt5.QtWebEngineWidgets import QWebEngineSettings

try:
    from PyQt5.QtPrintSupport import QPrinter, QPrintDialog
    HAS_PRINT = True
except ImportError:
    HAS_PRINT = False

APP_ORG = "Blowser"
APP_NAME = "Blowser"
DB_PATH = os.path.join(os.path.expanduser("~"), ".blowser_data.db")
HOME_URL_DEFAULT = "https://yandex.com.tr"

# ---- Firefox Photon renk paleti (koyu tema) ------------------------------ #
C_TOOLBAR = "#2b2a33"
C_TABSTRIP = "#1c1b22"
C_TAB_ACTIVE = "#42414d"
C_TAB_HOVER = "#3a3944"
C_TEXT = "#fbfbfe"
C_TEXT_MUTED = "#cfcfd8"
C_BORDER = "#52525e"
C_ACCENT = "#0060df"
C_ACCENT_HOVER = "#0250bb"
C_URLBAR_BG = "#42414d"
C_CONTENT_BG = "#2a2a2e"


# --------------------------------------------------------------------------- #
# Veritabanı
# --------------------------------------------------------------------------- #
class Database:
    """SQLite tabanlı yer imi / geçmiş yöneticisi."""

    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.cursor = self.conn.cursor()
        self._create_tables()

    def _create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE,
                title TEXT,
                date_added TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                title TEXT,
                date_visited TEXT,
                visit_count INTEGER DEFAULT 1
            )
        ''')
        self.conn.commit()

    def add_bookmark(self, url, title):
        try:
            self.cursor.execute(
                "INSERT OR REPLACE INTO bookmarks (url, title, date_added) VALUES (?, ?, ?)",
                (url, title or url, datetime.now().isoformat())
            )
            self.conn.commit()
            return True
        except sqlite3.Error:
            return False

    def remove_bookmark(self, url):
        self.cursor.execute("DELETE FROM bookmarks WHERE url = ?", (url,))
        self.conn.commit()

    def is_bookmarked(self, url):
        self.cursor.execute("SELECT 1 FROM bookmarks WHERE url = ?", (url,))
        return self.cursor.fetchone() is not None

    def get_bookmarks(self, query=None, limit=None):
        sql = "SELECT url, title, date_added FROM bookmarks"
        params = ()
        if query:
            like = f"%{query}%"
            sql += " WHERE url LIKE ? OR title LIKE ?"
            params = (like, like)
        sql += " ORDER BY date_added DESC"
        if limit:
            sql += " LIMIT ?"
            params = params + (limit,)
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()

    def add_history(self, url, title):
        self.cursor.execute("SELECT visit_count FROM history WHERE url = ?", (url,))
        result = self.cursor.fetchone()
        if result:
            self.cursor.execute(
                "UPDATE history SET visit_count = ?, date_visited = ?, title = ? WHERE url = ?",
                (result[0] + 1, datetime.now().isoformat(), title, url)
            )
        else:
            self.cursor.execute(
                "INSERT INTO history (url, title, date_visited) VALUES (?, ?, ?)",
                (url, title, datetime.now().isoformat())
            )
        self.conn.commit()

    def get_history(self, limit=200, query=None):
        if query:
            like = f"%{query}%"
            self.cursor.execute(
                "SELECT url, title, date_visited, visit_count FROM history "
                "WHERE url LIKE ? OR title LIKE ? ORDER BY date_visited DESC LIMIT ?",
                (like, like, limit)
            )
        else:
            self.cursor.execute(
                "SELECT url, title, date_visited, visit_count FROM history "
                "ORDER BY date_visited DESC LIMIT ?", (limit,)
            )
        return self.cursor.fetchall()

    def clear_history(self):
        self.cursor.execute("DELETE FROM history")
        self.conn.commit()

    def close(self):
        self.conn.close()


# --------------------------------------------------------------------------- #
# İndirmeler
# --------------------------------------------------------------------------- #
class DownloadRow(QWidget):
    def __init__(self, download_item, parent=None):
        super().__init__(parent)
        self.download_item = download_item
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)

        name = os.path.basename(download_item.path())
        self.name_label = QLabel(f"📄 {name}")
        self.name_label.setStyleSheet(f"color:{C_TEXT}; font-weight:600;")
        layout.addWidget(self.name_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.progress.setStyleSheet(f"""
            QProgressBar {{ background:{C_CONTENT_BG}; border:1px solid {C_BORDER};
                           border-radius:4px; color:white; text-align:center; height:16px; }}
            QProgressBar::chunk {{ background:{C_ACCENT}; border-radius:4px; }}
        """)
        layout.addWidget(self.progress)

        self.status_label = QLabel("İndiriliyor...")
        self.status_label.setStyleSheet(f"color:{C_TEXT_MUTED}; font-size:11px;")
        layout.addWidget(self.status_label)

        download_item.downloadProgress.connect(self.update_progress)
        download_item.finished.connect(self.finished)

    def update_progress(self, received, total):
        if total > 0:
            pct = int(received * 100 / total)
            self.progress.setValue(pct)
            self.status_label.setText(f"{received // 1024} KB / {total // 1024} KB")
        else:
            self.progress.setRange(0, 0)

    def finished(self):
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status_label.setText("✅ Tamamlandı: " + self.download_item.path())


class DownloadsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("İndirilenler")
        self.setGeometry(260, 260, 460, 420)
        self.setStyleSheet(f"QDialog {{ background:{C_TOOLBAR}; color:{C_TEXT}; }}")
        outer = QVBoxLayout(self)
        title = QLabel("📥 İndirilenler")
        title.setStyleSheet(f"color:{C_TEXT}; font-size:15px; font-weight:600; padding:4px;")
        outer.addWidget(title)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("QScrollArea { border:none; }")
        inner = QWidget()
        self.inner_layout = QVBoxLayout(inner)
        self.inner_layout.addStretch()
        self.scroll.setWidget(inner)
        outer.addWidget(self.scroll)

        self.empty_label = QLabel("Henüz indirme yok.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(f"color:{C_TEXT_MUTED}; padding:30px;")
        self.inner_layout.insertWidget(0, self.empty_label)

    def add_download(self, download_item):
        self.empty_label.hide()
        row = DownloadRow(download_item)
        self.inner_layout.insertWidget(self.inner_layout.count() - 1, row)


# --------------------------------------------------------------------------- #
# Sayfa içi arama çubuğu
# --------------------------------------------------------------------------- #
class FindBar(QWidget):
    def __init__(self, get_view_callback, parent=None):
        super().__init__(parent)
        self.get_view = get_view_callback
        self.setStyleSheet(f"""
            QWidget {{ background:{C_TOOLBAR}; border-top:1px solid {C_BORDER}; }}
            QLineEdit {{ background:{C_URLBAR_BG}; color:{C_TEXT}; border:1px solid {C_BORDER};
                        border-radius:4px; padding:4px 8px; }}
            QPushButton {{ background:transparent; color:{C_TEXT_MUTED}; border:none;
                          padding:4px 10px; border-radius:4px; }}
            QPushButton:hover {{ background:rgba(255,255,255,0.12); }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Sayfada bul...")
        self.input.textChanged.connect(lambda t: self.find(t))
        self.input.returnPressed.connect(lambda: self.find(self.input.text()))
        layout.addWidget(self.input)

        prev_btn = QPushButton("▲")
        prev_btn.clicked.connect(lambda: self.find(self.input.text(), backward=True))
        layout.addWidget(prev_btn)

        next_btn = QPushButton("▼")
        next_btn.clicked.connect(lambda: self.find(self.input.text()))
        layout.addWidget(next_btn)

        layout.addStretch()

        close_btn = QPushButton("✕")
        close_btn.clicked.connect(self.hide_bar)
        layout.addWidget(close_btn)

        self.hide()

    def show_bar(self):
        self.show()
        self.input.setFocus()
        self.input.selectAll()

    def hide_bar(self):
        view = self.get_view()
        if view:
            view.findText("")
        self.hide()

    def find(self, text, backward=False):
        view = self.get_view()
        if not view:
            return
        flags = QWebEnginePage.FindFlags()
        if backward:
            flags |= QWebEnginePage.FindBackward
        view.findText(text, flags)


# --------------------------------------------------------------------------- #
# Özel sayfa: yeni pencere/sekme istekleri (window.open, target=_blank,
# resimlere/linklere orta tık vb.) ana pencerede yeni sekme olarak açılır.
# Bu olmadan QWebEnginePage isteği geçerli sekmede açar (siteye "atlama" hissi).
# --------------------------------------------------------------------------- #
class BrowserPage(QWebEnginePage):
    def __init__(self, profile, window, parent=None):
        super().__init__(profile, parent)
        self._window = window

    def createWindow(self, _type):
        private = self.profile() is self._window.private_profile
        new_tab = self._window.add_new_tab(private=private)
        return new_tab.view.page()


# --------------------------------------------------------------------------- #
# Tek sekme: web görünümü + ilerleme çubuğu + arama çubuğu
# --------------------------------------------------------------------------- #
class BrowserTab(QWidget):
    def __init__(self, profile, window, url=None):
        super().__init__()
        self.zoom_level = 1.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{ background: transparent; border: none; }}
            QProgressBar::chunk {{ background: {C_ACCENT}; }}
        """)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.view = QWebEngineView()
        self.view.setPage(BrowserPage(profile, window, self.view))
        self.view.settings().setAttribute(QWebEngineSettings.FullScreenSupportEnabled, True)
        self.view.page().fullScreenRequested.connect(window.handle_fullscreen_request)
        layout.addWidget(self.view)

        self.find_bar = FindBar(lambda: self.view)
        layout.addWidget(self.find_bar)

        self.view.loadStarted.connect(self._on_load_started)
        self.view.loadProgress.connect(self.progress_bar.setValue)
        self.view.loadFinished.connect(self._on_load_finished)

        if url is None:
            url = QUrl(HOME_URL_DEFAULT)
        elif isinstance(url, str):
            url = QUrl(url)
        self.view.setUrl(url)

    def _on_load_started(self):
        self.progress_bar.show()
        self.progress_bar.setValue(0)

    def _on_load_finished(self, ok):
        self.progress_bar.hide()

    def toggle_find_bar(self):
        if self.find_bar.isVisible():
            self.find_bar.hide_bar()
        else:
            self.find_bar.show_bar()


# --------------------------------------------------------------------------- #
# Yer imleri çubuğu (Firefox'taki gibi adres çubuğunun altında)
# --------------------------------------------------------------------------- #
class BookmarksBar(QWidget):
    def __init__(self, db: Database, on_open):
        super().__init__()
        self.db = db
        self.on_open = on_open
        self.setFixedHeight(28)
        self.setStyleSheet(f"background:{C_TOOLBAR}; border-bottom:1px solid {C_BORDER};")
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(6, 0, 6, 0)
        self.layout.setSpacing(2)
        self.layout.addStretch()
        self.refresh()

    def refresh(self):
        while self.layout.count() > 1:
            item = self.layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        bookmarks = self.db.get_bookmarks(limit=25)
        for url, title, _ in reversed(bookmarks):
            label = (title or url).strip()
            if len(label) > 22:
                label = label[:19] + "..."
            btn = QToolButton()
            btn.setText("🔖 " + label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QToolButton {{ background:transparent; color:{C_TEXT_MUTED};
                               border:none; padding:3px 8px; border-radius:3px; font-size:12px; }}
                QToolButton:hover {{ background:rgba(255,255,255,0.1); color:{C_TEXT}; }}
            """)
            btn.clicked.connect(lambda checked=False, u=url: self.on_open(u))
            self.layout.insertWidget(self.layout.count() - 1, btn)


# --------------------------------------------------------------------------- #
# Ana pencere
# --------------------------------------------------------------------------- #
class Blowser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1280, 800)

        self.settings = QSettings(APP_ORG, APP_NAME)
        self.home_url = self.settings.value("home_url", HOME_URL_DEFAULT)
        self.search_engine = self.settings.value("search_engine", "Yandex")
        self.show_bookmarks_bar = self.settings.value("show_bookmarks_bar", True, type=bool)

        self.db = Database()
        self.downloads_dialog = DownloadsDialog(self)

        self.normal_profile = QWebEngineProfile.defaultProfile()
        self.normal_profile.downloadRequested.connect(self.handle_download)

        self.private_profile = QWebEngineProfile()  # bellek içi / kalıcı olmayan
        self.private_profile.downloadRequested.connect(self.handle_download)

        self.setWindowIcon(QIcon.fromTheme("web-browser"))
        self._build_ui()
        self._setup_shortcuts()

        self.add_new_tab(self.home_url)

    def _flash_status(self, message, timeout=2500):
        """Alt durum çubuğu kaldırıldı; olaylar artık pencere başlığında kısa süreliğine görünür."""
        # Sessizce yut - istenirse burada bir toast/overlay eklenebilir.
        pass

    # ------------------------------------------------------------------ #
    # Arayüz kurulumu
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.tabstrip_widget = self._build_tabstrip()
        root.addWidget(self.tabstrip_widget)

        self.toolbar_widget = self._build_toolbar()
        root.addWidget(self.toolbar_widget)

        self.bookmarks_bar = BookmarksBar(self.db, self._open_url_in_new_tab)
        self.bookmarks_bar.setVisible(self.show_bookmarks_bar)
        root.addWidget(self.bookmarks_bar)

        self.tabs = QTabWidget()
        self.tabs.tabBar().setVisible(False)  # sekme şeridini kendi widget'ımız gösteriyor
        self.tabs.setDocumentMode(True)
        self.tabs.setStyleSheet(f"QTabWidget::pane {{ border:none; background:{C_CONTENT_BG}; }}")
        root.addWidget(self.tabs)

        self._is_fullscreen_video = False
        self._was_maximized_before_fullscreen = False

    def _build_tabstrip(self):
        """Firefox tarzı sekme şeridi: özel QTabBar + yeni sekme butonu."""
        strip = QWidget()
        strip.setFixedHeight(36)
        strip.setStyleSheet(f"background:{C_TABSTRIP};")
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(4, 4, 4, 0)
        layout.setSpacing(0)

        self.tabbar = QTabBar()
        self.tabbar.setTabsClosable(True)
        self.tabbar.setMovable(True)
        self.tabbar.setExpanding(False)
        self.tabbar.setDrawBase(False)
        self.tabbar.setElideMode(Qt.ElideRight)
        self.tabbar.setIconSize(QSize(16, 16))
        self.tabbar.setStyleSheet(f"""
            QTabBar::tab {{
                background: transparent; color:{C_TEXT_MUTED};
                padding: 7px 14px; margin-right:2px; min-width:140px; max-width:220px;
                border-top-left-radius:8px; border-top-right-radius:8px;
            }}
            QTabBar::tab:selected {{ background:{C_TAB_ACTIVE}; color:{C_TEXT}; }}
            QTabBar::tab:hover:!selected {{ background:{C_TAB_HOVER}; }}
            QTabBar::close-button {{ subcontrol-position: right; }}
        """)
        self.tabbar.currentChanged.connect(self._on_tabbar_changed)
        self.tabbar.tabCloseRequested.connect(self.close_tab)
        self.tabbar.tabMoved.connect(self._on_tab_moved)
        layout.addWidget(self.tabbar, 1)

        new_tab_btn = QToolButton()
        new_tab_btn.setText("+")
        new_tab_btn.setFixedSize(28, 28)
        new_tab_btn.setCursor(Qt.PointingHandCursor)
        new_tab_btn.setStyleSheet(f"""
            QToolButton {{ background:transparent; color:{C_TEXT_MUTED}; border:none;
                          font-size:18px; font-weight:bold; border-radius:4px; }}
            QToolButton:hover {{ background:rgba(255,255,255,0.12); color:{C_TEXT}; }}
        """)
        new_tab_btn.clicked.connect(lambda: self.add_new_tab())
        layout.addWidget(new_tab_btn)

        return strip

    def _build_toolbar(self):
        bar = QWidget()
        bar.setFixedHeight(42)
        bar.setStyleSheet(f"background:{C_TOOLBAR}; border-bottom:1px solid {C_BORDER};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        nav_style = f"""
            QToolButton {{ background:transparent; color:{C_TEXT_MUTED}; border:none;
                          padding:6px; border-radius:16px; font-size:15px; }}
            QToolButton:hover {{ background:rgba(255,255,255,0.1); color:{C_TEXT}; }}
            QToolButton:disabled {{ color:#5c5b66; }}
        """

        self.back_btn = QToolButton()
        self.back_btn.setText("◀")
        self.back_btn.setFixedSize(32, 32)
        self.back_btn.setStyleSheet(nav_style)
        self.back_btn.clicked.connect(self.go_back)
        layout.addWidget(self.back_btn)

        self.forward_btn = QToolButton()
        self.forward_btn.setText("▶")
        self.forward_btn.setFixedSize(32, 32)
        self.forward_btn.setStyleSheet(nav_style)
        self.forward_btn.clicked.connect(self.go_forward)
        layout.addWidget(self.forward_btn)

        self.reload_btn = QToolButton()
        self.reload_btn.setText("⟳")
        self.reload_btn.setFixedSize(32, 32)
        self.reload_btn.setStyleSheet(nav_style)
        self.reload_btn.clicked.connect(self.reload_page)
        layout.addWidget(self.reload_btn)

        self.home_btn = QToolButton()
        self.home_btn.setText("🏠")
        self.home_btn.setFixedSize(32, 32)
        self.home_btn.setStyleSheet(nav_style)
        self.home_btn.clicked.connect(self.go_home)
        layout.addWidget(self.home_btn)

        layout.addSpacing(6)

        # --- Adres çubuğu (Firefox tarzı: kilit + url + yıldız içeride) --- #
        url_container = QWidget()
        url_container.setStyleSheet(f"""
            QWidget {{ background:{C_URLBAR_BG}; border:1px solid {C_BORDER}; border-radius:16px; }}
        """)
        url_layout = QHBoxLayout(url_container)
        url_layout.setContentsMargins(10, 0, 6, 0)
        url_layout.setSpacing(6)

        self.security_label = QLabel("🔒")
        self.security_label.setStyleSheet("background:transparent; border:none; font-size:12px;")
        url_layout.addWidget(self.security_label)

        self.url_bar = QLineEdit()
        self.url_bar.setStyleSheet(f"""
            QLineEdit {{ background:transparent; color:{C_TEXT}; border:none;
                        padding:6px 0px; font-size:13px; selection-background-color:{C_ACCENT}; }}
        """)
        self.url_bar.setPlaceholderText("Arama yapın veya adres girin")
        self.url_bar.returnPressed.connect(self.navigate_to_url)
        url_layout.addWidget(self.url_bar, 1)

        self.bookmark_btn = QToolButton()
        self.bookmark_btn.setText("☆")
        self.bookmark_btn.setFixedSize(26, 26)
        self.bookmark_btn.setCursor(Qt.PointingHandCursor)
        self.bookmark_btn.setStyleSheet(f"""
            QToolButton {{ background:transparent; color:{C_TEXT_MUTED}; border:none;
                          font-size:15px; border-radius:4px; }}
            QToolButton:hover {{ background:rgba(255,255,255,0.12); color:#ffcc00; }}
        """)
        self.bookmark_btn.clicked.connect(self.toggle_bookmark)
        url_layout.addWidget(self.bookmark_btn)

        layout.addWidget(url_container, 1)

        layout.addSpacing(6)

        # --- Sağ taraf ikon grubu --- #
        icon_style = f"""
            QToolButton {{ background:transparent; color:{C_TEXT_MUTED}; border:none;
                          padding:6px; border-radius:16px; font-size:14px; }}
            QToolButton:hover {{ background:rgba(255,255,255,0.1); color:{C_TEXT}; }}
        """

        self.downloads_btn = QToolButton()
        self.downloads_btn.setText("⬇")
        self.downloads_btn.setFixedSize(32, 32)
        self.downloads_btn.setStyleSheet(icon_style)
        self.downloads_btn.clicked.connect(self.show_downloads_dialog)
        layout.addWidget(self.downloads_btn)

        self.library_btn = QToolButton()
        self.library_btn.setText("📚")
        self.library_btn.setFixedSize(32, 32)
        self.library_btn.setStyleSheet(icon_style)
        self.library_btn.setToolTip("Yer İmleri ve Geçmiş")
        self.library_btn.clicked.connect(self.show_history_dialog)
        layout.addWidget(self.library_btn)

        self.menu_btn = QToolButton()
        self.menu_btn.setText("☰")
        self.menu_btn.setFixedSize(32, 32)
        self.menu_btn.setStyleSheet(icon_style)
        self.menu_btn.clicked.connect(self.show_menu)
        layout.addWidget(self.menu_btn)

        return bar

    # ------------------------------------------------------------------ #
    # Sekme yönetimi (QTabWidget içerik deposu + özel QTabBar senkron)
    # ------------------------------------------------------------------ #
    def current_tab(self) -> BrowserTab:
        return self.tabs.currentWidget()

    def current_view(self) -> QWebEngineView:
        tab = self.current_tab()
        return tab.view if tab else None

    def add_new_tab(self, url=None, private=False):
        profile = self.private_profile if private else self.normal_profile
        tab = BrowserTab(profile, self, url)

        tab.view.urlChanged.connect(lambda qurl, t=tab: self._on_url_changed(t, qurl))
        tab.view.titleChanged.connect(lambda title, t=tab: self._on_title_changed(t, title))
        tab.view.iconChanged.connect(lambda icon, t=tab: self._on_icon_changed(t, icon))
        tab.view.loadFinished.connect(lambda ok, t=tab: self._on_page_loaded(t, ok))

        content_index = self.tabs.addTab(tab, "")
        label = "🕶️ Özel Sekme" if private else "Yeni Sekme"
        tab_index = self.tabbar.addTab(label)
        tab.is_private = private

        self.tabbar.setCurrentIndex(tab_index)
        self.tabs.setCurrentIndex(content_index)
        return tab

    def add_private_tab(self):
        self.add_new_tab(private=True)
        self._flash_status("🕶️ Özel gezinti - geçmiş kaydedilmiyor")

    def _open_url_in_new_tab(self, url):
        view = self.current_view()
        if view:
            view.setUrl(QUrl(url))
        else:
            self.add_new_tab(url)

    def close_tab(self, index):
        if self.tabbar.count() > 1:
            widget = self.tabs.widget(index)
            self.tabbar.removeTab(index)
            self.tabs.removeTab(index)
            if widget:
                widget.deleteLater()
        else:
            self.close()

    def _on_tab_moved(self, frm, to):
        # QTabWidget içeriğini de aynı sıraya taşı
        widget = self.tabs.widget(frm)
        self.tabs.tabBar().moveTab(frm, to) if False else None
        # QTabWidget'ın sekme çubuğu gizli olduğundan doğrudan indexleri senkronlamak
        # yerine widget'ı yeniden ekleyerek sırayı hizalıyoruz.
        w = self.tabs.widget(frm)
        if w is not None:
            self.tabs.removeTab(frm)
            self.tabs.insertTab(to, w, "")
        self.tabs.setCurrentIndex(self.tabbar.currentIndex())

    def _on_tabbar_changed(self, index):
        if index < 0:
            return
        self.tabs.setCurrentIndex(index)
        tab = self.tabs.widget(index)
        if not tab:
            return
        self.url_bar.setText(tab.view.url().toString())
        self._refresh_nav_buttons(tab)
        self.update_bookmark_button(tab.view.url().toString())
        self.update_security_icon(tab.view.url())

    def _on_url_changed(self, tab, qurl):
        if tab == self.current_tab():
            self.url_bar.setText(qurl.toString())
            self.update_bookmark_button(qurl.toString())
            self.update_security_icon(qurl)
        self._refresh_nav_buttons(tab)

    def _on_title_changed(self, tab, title):
        index = self.tabs.indexOf(tab)
        if index == -1:
            return
        display = title or tab.view.url().host() or "Yeni Sekme"
        if getattr(tab, "is_private", False):
            display = "🕶️ " + display
        self.tabbar.setTabText(index, display)
        self.tabbar.setTabToolTip(index, title or "")
        if index == self.tabbar.currentIndex():
            self.setWindowTitle(f"{title} - Blowser" if title else "Blowser")

    def _on_icon_changed(self, tab, icon):
        index = self.tabs.indexOf(tab)
        if index != -1 and icon and not icon.isNull():
            self.tabbar.setTabIcon(index, icon)

    def _on_page_loaded(self, tab, ok):
        if not ok:
            self._flash_status("⚠️ Sayfa yüklenemedi")
            return
        url = tab.view.url().toString()
        if not getattr(tab, "is_private", False) and url and not url.startswith("about:"):
            title = tab.view.page().title()
            self.db.add_history(url, title)
        if tab == self.current_tab():
            self.update_bookmark_button(url)
        self._flash_status("Hazır")

    # ------------------------------------------------------------------ #
    # Navigasyon
    # ------------------------------------------------------------------ #
    def handle_fullscreen_request(self, request):
        """YouTube gibi sitelerin video tam ekran isteğini karşılar.
        Bu olmadan video yalnızca pencere içinde büyür ve görev çubuğuyla
        çakışabilir; burada gerçek işletim sistemi tam ekranına geçiyoruz."""
        request.accept()
        if request.toggleOn():
            self._enter_fullscreen()
        else:
            self._exit_fullscreen()

    def _enter_fullscreen(self):
        if self._is_fullscreen_video:
            return
        self._is_fullscreen_video = True
        self._was_maximized_before_fullscreen = self.isMaximized()
        self.tabstrip_widget.hide()
        self.toolbar_widget.hide()
        self.bookmarks_bar.hide()
        self.showFullScreen()

    def _exit_fullscreen(self):
        if not self._is_fullscreen_video:
            return
        self._is_fullscreen_video = False
        self.tabstrip_widget.show()
        self.toolbar_widget.show()
        self.bookmarks_bar.setVisible(self.show_bookmarks_bar)
        if self._was_maximized_before_fullscreen:
            self.showMaximized()
        else:
            self.showNormal()

    def navigate_to_url(self):
        text = self.url_bar.text().strip()
        if not text:
            return

        looks_like_url = (
            text.startswith("http://") or text.startswith("https://")
            or text.startswith("file://") or text.startswith("about:")
            or ("." in text and " " not in text)
        )

        if looks_like_url:
            url = text if "://" in text or text.startswith("about:") else "https://" + text
        else:
            engines = {
                "Yandex": "https://yandex.com.tr/search/?text=",
                "Bing": "https://www.bing.com/search?q=",
                "DuckDuckGo": "https://duckduckgo.com/?q=",
            }
            base = engines.get(self.search_engine, engines["Yandex"])
            url = base + text.replace(" ", "+")

        view = self.current_view()
        if view:
            view.setUrl(QUrl(url))

    def go_back(self):
        view = self.current_view()
        if view and view.history().canGoBack():
            view.back()

    def go_forward(self):
        view = self.current_view()
        if view and view.history().canGoForward():
            view.forward()

    def reload_page(self):
        view = self.current_view()
        if view:
            view.reload()

    def go_home(self):
        view = self.current_view()
        if view:
            view.setUrl(QUrl(self.home_url))

    def _refresh_nav_buttons(self, tab):
        if tab == self.current_tab():
            self.back_btn.setEnabled(tab.view.history().canGoBack())
            self.forward_btn.setEnabled(tab.view.history().canGoForward())

    def update_security_icon(self, qurl: QUrl):
        scheme = qurl.scheme()
        if scheme == "https":
            self.security_label.setText("🔒")
            self.security_label.setToolTip("Güvenli bağlantı (HTTPS)")
        elif scheme == "http":
            self.security_label.setText("⚠️")
            self.security_label.setToolTip("Güvensiz bağlantı (HTTP)")
        else:
            self.security_label.setText("")
            self.security_label.setToolTip("")

    # ------------------------------------------------------------------ #
    # Yakınlaştırma
    # ------------------------------------------------------------------ #
    def zoom_page(self, delta):
        tab = self.current_tab()
        if not tab:
            return
        tab.zoom_level = max(0.25, min(3.0, tab.zoom_level + delta))
        tab.view.setZoomFactor(tab.zoom_level)
        self._flash_status(f"Yakınlaştırma: {int(tab.zoom_level * 100)}%", 2000)

    def reset_zoom(self):
        tab = self.current_tab()
        if not tab:
            return
        tab.zoom_level = 1.0
        tab.view.setZoomFactor(1.0)

    # ------------------------------------------------------------------ #
    # Yer imleri
    # ------------------------------------------------------------------ #
    def toggle_bookmark(self):
        view = self.current_view()
        if not view:
            return
        url = view.url().toString()
        title = view.page().title()

        if self.db.is_bookmarked(url):
            self.db.remove_bookmark(url)
            self.bookmark_btn.setText("☆")
            self._flash_status("Yer imi kaldırıldı")
        else:
            if self.db.add_bookmark(url, title):
                self.bookmark_btn.setText("★")
                self._flash_status("Yer imi eklendi")
            else:
                QMessageBox.warning(self, "Hata", "Yer imi eklenemedi!")
        self.bookmarks_bar.refresh()

    def update_bookmark_button(self, url):
        self.bookmark_btn.setText("★" if self.db.is_bookmarked(url) else "☆")

    def show_bookmarks_dialog(self):
        dialog = self._make_list_dialog("⭐ Yer İmleri")
        list_widget: QListWidget = dialog.list_widget

        def reload_list(query=None):
            list_widget.clear()
            for url, title, date in self.db.get_bookmarks(query):
                item = QListWidgetItem(f"🔖 {title or url}\n{url}")
                item.setData(Qt.UserRole, url)
                list_widget.addItem(item)

        dialog.search_box.textChanged.connect(reload_list)
        reload_list()

        def open_selected():
            item = list_widget.currentItem()
            if item:
                self._open_url_in_new_tab(item.data(Qt.UserRole))
                dialog.close()

        def delete_selected():
            item = list_widget.currentItem()
            if item:
                self.db.remove_bookmark(item.data(Qt.UserRole))
                list_widget.takeItem(list_widget.currentRow())
                self.bookmarks_bar.refresh()
                self._flash_status("Yer imi silindi")

        dialog.open_btn.clicked.connect(open_selected)
        dialog.action_btn.setText("Sil")
        dialog.action_btn.clicked.connect(delete_selected)
        dialog.exec_()

    # ------------------------------------------------------------------ #
    # Geçmiş
    # ------------------------------------------------------------------ #
    def show_history_dialog(self):
        dialog = self._make_list_dialog("📜 Geçmiş")
        list_widget: QListWidget = dialog.list_widget

        def reload_list(query=None):
            list_widget.clear()
            for url, title, date, count in self.db.get_history(200, query):
                item = QListWidgetItem(
                    f"📄 {title or url}\n{url}\n📅 {date[:16].replace('T', ' ')} | {count} ziyaret"
                )
                item.setData(Qt.UserRole, url)
                list_widget.addItem(item)

        dialog.search_box.textChanged.connect(reload_list)
        reload_list()

        def open_selected():
            item = list_widget.currentItem()
            if item:
                self._open_url_in_new_tab(item.data(Qt.UserRole))
                dialog.close()

        def clear_all():
            reply = QMessageBox.question(
                self, "Temizle", "Tüm geçmişi silmek istediğinize emin misiniz?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.db.clear_history()
                list_widget.clear()
                self._flash_status("Geçmiş temizlendi")

        dialog.open_btn.clicked.connect(open_selected)
        dialog.action_btn.setText("Tümünü Temizle")
        dialog.action_btn.setStyleSheet(
            "QPushButton { background:#c50042; } QPushButton:hover { background:#9a0031; }"
        )
        dialog.action_btn.clicked.connect(clear_all)
        dialog.exec_()

    def _make_list_dialog(self, title):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setGeometry(240, 240, 620, 460)
        dialog.setStyleSheet(f"""
            QDialog {{ background:{C_TOOLBAR}; color:{C_TEXT}; }}
            QLineEdit {{ background:{C_CONTENT_BG}; color:{C_TEXT}; border:1px solid {C_BORDER};
                        border-radius:14px; padding:6px 12px; }}
            QListWidget {{ background:{C_CONTENT_BG}; border:1px solid {C_BORDER};
                          border-radius:4px; padding:5px; }}
            QListWidget::item {{ padding:8px; border-radius:4px; }}
            QListWidget::item:selected {{ background:{C_TAB_ACTIVE}; }}
            QPushButton {{ background:{C_ACCENT}; color:white; border:none;
                          padding:8px 16px; border-radius:4px; }}
            QPushButton:hover {{ background:{C_ACCENT_HOVER}; }}
        """)
        layout = QVBoxLayout(dialog)

        search_box = QLineEdit()
        search_box.setPlaceholderText("🔍 Ara...")
        layout.addWidget(search_box)

        list_widget = QListWidget()
        list_widget.itemDoubleClicked.connect(lambda _: dialog.open_btn.click())
        layout.addWidget(list_widget)

        btn_layout = QHBoxLayout()
        open_btn = QPushButton("Aç")
        btn_layout.addWidget(open_btn)
        action_btn = QPushButton("Sil")
        btn_layout.addWidget(action_btn)
        close_btn = QPushButton("Kapat")
        close_btn.clicked.connect(dialog.close)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dialog.search_box = search_box
        dialog.list_widget = list_widget
        dialog.open_btn = open_btn
        dialog.action_btn = action_btn
        return dialog

    # ------------------------------------------------------------------ #
    # İndirmeler
    # ------------------------------------------------------------------ #
    def handle_download(self, download_item):
        default_dir = os.path.expanduser("~/Downloads")
        if not os.path.isdir(default_dir):
            default_dir = os.path.expanduser("~")
        suggested = (
            download_item.downloadFileName()
            if hasattr(download_item, "downloadFileName")
            else os.path.basename(download_item.path())
        )
        target, _ = QFileDialog.getSaveFileName(
            self, "Dosyayı Kaydet", os.path.join(default_dir, suggested)
        )
        if not target:
            download_item.cancel()
            return
        download_item.setPath(target)
        download_item.accept()
        self.downloads_dialog.add_download(download_item)
        self._flash_status(f"İndirme başladı: {os.path.basename(target)}")

    def show_downloads_dialog(self):
        self.downloads_dialog.show()
        self.downloads_dialog.raise_()
        self.downloads_dialog.activateWindow()

    # ------------------------------------------------------------------ #
    # Yazdırma / sayfada bul
    # ------------------------------------------------------------------ #
    def print_page(self):
        if not HAS_PRINT:
            QMessageBox.information(self, "Yazdır", "Bu sistemde yazdırma desteği bulunamadı.")
            return
        view = self.current_view()
        if not view:
            return
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec_() == QPrintDialog.Accepted:
            view.page().print(
                printer,
                lambda success: self._flash_status(
                    "Yazdırma " + ("tamamlandı" if success else "başarısız")
                )
            )

    def find_on_page(self):
        tab = self.current_tab()
        if tab:
            tab.toggle_find_bar()

    # ------------------------------------------------------------------ #
    # Tema / ayarlar / menü
    # ------------------------------------------------------------------ #
    def toggle_bookmarks_bar(self):
        self.show_bookmarks_bar = not self.show_bookmarks_bar
        self.bookmarks_bar.setVisible(self.show_bookmarks_bar)
        self.settings.setValue("show_bookmarks_bar", self.show_bookmarks_bar)

    def show_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Ayarlar")
        dialog.setGeometry(320, 320, 420, 320)
        dialog.setStyleSheet(f"""
            QDialog {{ background:{C_TOOLBAR}; color:{C_TEXT}; }}
            QLabel {{ color:{C_TEXT_MUTED}; }}
            QLineEdit, QComboBox {{ background:{C_CONTENT_BG}; color:{C_TEXT}; padding:6px;
                                    border:1px solid {C_BORDER}; border-radius:4px; }}
            QPushButton {{ background:{C_ACCENT}; color:white; border:none;
                          padding:8px 16px; border-radius:4px; }}
            QPushButton:hover {{ background:{C_ACCENT_HOVER}; }}
        """)
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel("🏠 Ana Sayfa:"))
        home_input = QLineEdit(self.home_url)
        layout.addWidget(home_input)

        layout.addWidget(QLabel("🔍 Varsayılan Arama Motoru:"))
        search_combo = QComboBox()
        search_combo.addItems(["Yandex", "Bing", "DuckDuckGo"])
        search_combo.setCurrentText(self.search_engine)
        layout.addWidget(search_combo)

        def apply_search_engine(text):
            self.search_engine = text
            self.settings.setValue("search_engine", self.search_engine)
            self._flash_status(f"Arama motoru: {text}")

        search_combo.currentTextChanged.connect(apply_search_engine)

        layout.addStretch()
        save_btn = QPushButton("💾 Kaydet")

        def save_and_close():
            self.home_url = home_input.text().strip() or HOME_URL_DEFAULT
            self.search_engine = search_combo.currentText()
            self.settings.setValue("home_url", self.home_url)
            self.settings.setValue("search_engine", self.search_engine)
            self._flash_status("Ayarlar kaydedildi")
            dialog.close()

        save_btn.clicked.connect(save_and_close)
        layout.addWidget(save_btn)
        dialog.exec_()

    def show_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background:{C_TOOLBAR}; color:{C_TEXT_MUTED}; border:1px solid {C_BORDER}; padding:4px; }}
            QMenu::item {{ padding:6px 24px; border-radius:4px; }}
            QMenu::item:selected {{ background:{C_TAB_ACTIVE}; color:{C_TEXT}; }}
            QMenu::separator {{ height:1px; background:{C_BORDER}; margin:4px 8px; }}
        """)

        menu.addAction("📑  Yeni Sekme\tCtrl+T", lambda: self.add_new_tab())
        menu.addAction("🕶️  Yeni Özel Pencere\tCtrl+Shift+P", self.add_private_tab)
        menu.addSeparator()
        menu.addAction("⭐  Yer İmleri\tCtrl+Shift+B", self.show_bookmarks_dialog)
        menu.addAction("📜  Geçmiş\tCtrl+H", self.show_history_dialog)
        menu.addAction("⬇  İndirilenler\tCtrl+J", self.show_downloads_dialog)
        bm_bar_action = menu.addAction("📌  Yer İmleri Çubuğu", self.toggle_bookmarks_bar)
        bm_bar_action.setCheckable(True)
        bm_bar_action.setChecked(self.show_bookmarks_bar)
        menu.addSeparator()
        menu.addAction("🔍  Sayfada Bul\tCtrl+F", self.find_on_page)
        menu.addAction("🖨️  Yazdır\tCtrl+P", self.print_page)
        zoom_menu = menu.addMenu("🔎  Yakınlaştır")
        zoom_menu.addAction("Yakınlaştır (+)\tCtrl++", lambda: self.zoom_page(0.1))
        zoom_menu.addAction("Uzaklaştır (-)\tCtrl+-", lambda: self.zoom_page(-0.1))
        zoom_menu.addAction("Sıfırla\tCtrl+0", self.reset_zoom)
        menu.addSeparator()
        menu.addAction("⚙️  Ayarlar", self.show_settings)
        menu.addSeparator()
        menu.addAction("❌  Çıkış\tCtrl+Q", self.close)

        menu.exec_(self.menu_btn.mapToGlobal(self.menu_btn.rect().bottomRight()))

    # ------------------------------------------------------------------ #
    # Kısayollar
    # ------------------------------------------------------------------ #
    def _setup_shortcuts(self):
        shortcuts = [
            ("Ctrl+T", lambda: self.add_new_tab()),
            ("Ctrl+W", lambda: self.close_tab(self.tabbar.currentIndex())),
            ("F5", self.reload_page),
            ("Ctrl+R", self.reload_page),
            ("Ctrl+Shift+P", self.add_private_tab),
            ("Alt+Left", self.go_back),
            ("Alt+Right", self.go_forward),
            ("Ctrl+Shift+H", self.go_home),
            ("Ctrl+L", self.url_bar.setFocus),
            ("Ctrl+H", self.show_history_dialog),
            ("Ctrl+Shift+B", self.show_bookmarks_dialog),
            ("Ctrl+J", self.show_downloads_dialog),
            ("Ctrl+D", self.toggle_bookmark),
            ("Ctrl+P", self.print_page),
            ("Ctrl+F", self.find_on_page),
            ("Escape", self._escape_pressed),
            ("Ctrl+Q", self.close),
            ("Ctrl+=", lambda: self.zoom_page(0.1)),
            ("Ctrl++", lambda: self.zoom_page(0.1)),
            ("Ctrl+-", lambda: self.zoom_page(-0.1)),
            ("Ctrl+0", self.reset_zoom),
            ("Ctrl+Tab", self._next_tab),
            ("Ctrl+Shift+Tab", self._prev_tab),
            ("Ctrl+PgDown", self._next_tab),
            ("Ctrl+PgUp", self._prev_tab),
        ]
        for key, func in shortcuts:
            QShortcut(QKeySequence(key), self).activated.connect(func)

    def _next_tab(self):
        count = self.tabbar.count()
        if count > 1:
            self.tabbar.setCurrentIndex((self.tabbar.currentIndex() + 1) % count)

    def _prev_tab(self):
        count = self.tabbar.count()
        if count > 1:
            self.tabbar.setCurrentIndex((self.tabbar.currentIndex() - 1) % count)

    def _escape_pressed(self):
        if self._is_fullscreen_video:
            view = self.current_view()
            if view:
                view.triggerPageAction(QWebEnginePage.ExitFullScreen)
            self._exit_fullscreen()
            return
        tab = self.current_tab()
        if tab and tab.find_bar.isVisible():
            tab.find_bar.hide_bar()

    # ------------------------------------------------------------------ #
    def closeEvent(self, event):
        self.db.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ORG)
    app.setStyle("Fusion")

    app.setStyleSheet(f"""
        QWidget {{ background-color:{C_CONTENT_BG}; color:{C_TEXT}; }}
        QScrollBar:vertical {{ background:{C_TOOLBAR}; width:12px; }}
        QScrollBar::handle:vertical {{ background:#5c5b66; border-radius:6px; min-height:24px; }}
        QScrollBar::handle:vertical:hover {{ background:#726f7c; }}
        QScrollBar:horizontal {{ background:{C_TOOLBAR}; height:12px; }}
        QScrollBar::handle:horizontal {{ background:#5c5b66; border-radius:6px; }}
        QToolTip {{ background:{C_TOOLBAR}; color:{C_TEXT}; border:1px solid {C_BORDER}; padding:4px; }}
    """)

    window = Blowser()
    window.setWindowTitle("Blowser")
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
