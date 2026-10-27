# ForGo — universal multi-game Windows optimizer
# Python 3.10+ / PySide6. Windows-only system tweaks are guarded at runtime.
from __future__ import annotations

import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import psutil
from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QTimer, QPropertyAnimation, Qt, Signal, Property)
from PySide6.QtGui import (QBrush, QColor, QFont, QIcon, QMouseEvent, QPainter,
                           QLinearGradient, QRadialGradient, QPixmap)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QGraphicsDropShadowEffect,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

APP_NAME = "ForGo"
APP_VERSION = "3.4.0"
IS_WINDOWS = os.name == "nt"

# Games are started through their official clients. System-level Windows tweaks are
# reversible and shared by Windows; process priority follows the currently selected game.
GAME_CATALOG: dict[str, dict[str, Any]] = {
    "fortnite": {
        "name": "Fortnite", "tag": "FN", "client": "Epic Games Launcher",
        "description": "Battle Royale · Epic Games", "kind": "epic",
        "process_names": ["fortniteclient-win64-shipping.exe", "fortniteclient-win64-shipping_be.exe"],
    },
    "cs2": {
        "name": "Counter-Strike 2", "tag": "CS2", "client": "Steam · App 730",
        "description": "Тактический соревновательный шутер", "kind": "steam", "appid": "730",
        "process_names": ["cs2.exe", "csgo.exe"],
    },
    "dota2": {
        "name": "Dota 2", "tag": "D2", "client": "Steam · App 570",
        "description": "Командная MOBA", "kind": "steam", "appid": "570",
        "process_names": ["dota2.exe"],
    },
    "valorant": {
        "name": "VALORANT", "tag": "V", "client": "Riot Client",
        "description": "Тактический шутер · Vanguard остаётся активным", "kind": "riot", "product": "valorant",
        "process_names": ["valorant-win64-shipping.exe", "valorant.exe"],
    },
    "apex": {
        "name": "Apex Legends", "tag": "APEX", "client": "Steam · App 1172470",
        "description": "Командный hero-шутер", "kind": "steam", "appid": "1172470",
        "process_names": ["r5apex.exe"],
    },
    "pubg": {
        "name": "PUBG: Battlegrounds", "tag": "PUBG", "client": "Steam · App 578080",
        "description": "Тактическая королевская битва", "kind": "steam", "appid": "578080",
        "process_names": ["tslgame.exe"],
    },
    "r6": {
        "name": "Rainbow Six Siege", "tag": "R6", "client": "Steam · App 359550",
        "description": "Тактический командный шутер", "kind": "steam", "appid": "359550",
        "process_names": ["rainbowsix.exe", "rainbowsix_vulkan.exe"],
    },
    "lol": {
        "name": "League of Legends", "tag": "LoL", "client": "Riot Client",
        "description": "Командная MOBA", "kind": "riot", "product": "league_of_legends",
        "process_names": ["league of legends.exe"],
    },
}

REGISTRY_TWEAKS: dict[str, list[tuple[str, str, Any, int]]] = {
    "game_mode": [
        (r"Software\Microsoft\GameBar", "AllowAutoGameMode", 1, 4),
        (r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1, 4),
    ],
    "game_dvr": [
        (r"System\GameConfigStore", "GameDVR_Enabled", 0, 4),
        (r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 0, 4),
    ],
    "visual_effects": [
        (r"Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects", "VisualFXSetting", 2, 4),
    ],
    "mouse_accel": [
        (r"Control Panel\Mouse", "MouseSpeed", "0", 1),
        (r"Control Panel\Mouse", "MouseThreshold1", "0", 1),
        (r"Control Panel\Mouse", "MouseThreshold2", "0", 1),
    ],
}

# Each entry is an individually reversible setting. Most entries are user-preference,
# privacy, or UI responsiveness controls; only a subset can affect gaming performance.
TWEAK_CATALOG: list[dict[str, Any]] = []

def _catalog_item(tweak_id: str, category: str, title: str, description: str,
                  registry: list[tuple[str, str, Any, int]] | None = None,
                  recommended: bool = False, warning: bool = False,
                  action_only: bool = False) -> None:
    TWEAK_CATALOG.append({
        "id": tweak_id, "category": category, "title": title,
        "description": description, "recommended": recommended,
        "warning": warning, "action_only": action_only,
    })
    if registry:
        REGISTRY_TWEAKS[tweak_id] = registry

_catalog_item("game_mode", "Игры и производительность", "Windows Game Mode",
              "Позволяет Windows лучше приоритизировать активную игру. Эффект зависит от системы.", REGISTRY_TWEAKS["game_mode"], True)
_catalog_item("game_dvr", "Игры и производительность", "Отключить фоновую запись Xbox",
              "Отключает запись игрового процесса в фоне. Не включай, если используешь запись клипов.", REGISTRY_TWEAKS["game_dvr"], True, True)
_catalog_item("power_plan", "Игры и производительность", "План высокой производительности",
              "Меняет план питания Windows; может увеличить температуру, шум и расход батареи.", recommended=False, warning=True, action_only=True)
_catalog_item("visual_effects", "Визуальная отзывчивость", "Уменьшить системные анимации",
              "Переключает системные визуальные эффекты на производительность. Внешний вид Windows станет проще.", REGISTRY_TWEAKS["visual_effects"], warning=True)
_catalog_item("mouse_accel", "Мышь и ввод", "Отключить ускорение мыши Windows",
              "Отключает системное ускорение указателя. Не меняет чувствительность мыши внутри игры.", REGISTRY_TWEAKS["mouse_accel"], warning=True)

# The extra controls are intentionally scoped to HKCU. No security services, anti-cheat,
# Windows Update, firewall, Defender, or protected game files are modified.
def _add_group(category: str, path: str, items: list[tuple]) -> None:
    for item in items:
        # id, title, description, registry value name, desired value, type[, recommended[, warning]]
        tid, title, desc, value_name, value, reg_type, *flags = item
        rec = bool(flags[0]) if flags else False
        warn = bool(flags[1]) if len(flags) > 1 else False
        _catalog_item(tid, category, title, desc, [(path, value_name, value, reg_type)], rec, warn)

_add_group("Игры и производительность", r"Software\Microsoft\GameBar", [
    ("gamebar_startup_panel", "Не показывать подсказку Game Bar", "Скрывает стартовую панель Xbox Game Bar.", "ShowStartupPanel", 0, 4, False, False),
    ("gamebar_nexus", "Отключить открытие Game Bar кнопкой Xbox", "Меняет поведение панели Game Bar; настройка может зависеть от версии Windows.", "UseNexusForGameBarEnabled", 0, 4, False, True),
    ("gamebar_friends", "Скрыть социальные функции Game Bar", "Отключает часть игровых социальных подсказок, если поддерживается системой.", "ShowGameModeNotifications", 0, 4, False, False),
])
_add_group("Игры и производительность", r"System\GameConfigStore", [
    ("game_fse_mode", "Настройка поведения полноэкранных игр", "Совместимость полноэкранного режима; верни значение, если игра ведёт себя хуже.", "GameDVR_FSEBehaviorMode", 2, 4, False, True),
    ("game_fse_user_mode", "Уважать режим полноэкранной игры", "Вспомогательная настройка совместимости полноэкранного режима.", "GameDVR_HonorUserFSEBehaviorMode", 1, 4, False, True),
    ("game_dxgi_fse", "Совместимость DXGI полноэкранного режима", "Параметр совместимости Windows Game DVR / DXGI; эффект зависит от сборки Windows.", "GameDVR_DXGIHonorFSEWindowsCompatible", 1, 4, False, True),
    ("game_fse_flags", "Сбросить дополнительные флаги Game DVR", "Служебный параметр полноэкранного режима; не гарантирует рост FPS.", "GameDVR_EFSEFeatureFlags", 0, 4, False, True),
])
_add_group("Визуальная отзывчивость", r"Control Panel\Desktop", [
    ("menu_delay", "Уменьшить задержку меню", "Ускоряет открытие системных меню; значение хранится как строка.", "MenuShowDelay", "100", 1, True, False),
    ("window_min_animate", "Отключить анимацию сворачивания окон", "Уменьшает анимацию окон Windows.", "MinAnimate", "0", 1, False, False),
    ("drag_full_windows", "Перетаскивать окна без содержимого", "Уменьшает отрисовку при перемещении окон; интерфейс будет менее плавным.", "DragFullWindows", "0", 1, False, True),
    ("font_smoothing", "Включить сглаживание шрифтов", "Повышает читаемость текста; это визуальная настройка, не FPS-твик.", "FontSmoothing", "2", 1, False, False),
    ("cursor_blink", "Ускорить мигание текстового курсора", "Меняет интервал мигания курсора в системных полях ввода.", "CursorBlinkRate", "530", 1, False, False),
])
_add_group("Мышь и ввод", r"Control Panel\Mouse", [
    ("mouse_hover_time", "Быстрее реакция наведения мыши", "Уменьшает задержку hover-событий Windows; может раздражать при случайном наведении.", "MouseHoverTime", "400", 1, False, True),
    ("mouse_trails_off", "Отключить следы указателя", "Убирает декоративный след указателя, если он включён.", "MouseTrails", "0", 1, False, False),
    ("mouse_snap_off", "Не перемещать указатель к кнопке по умолчанию", "Отключает системное Snap To для более предсказуемого управления мышью.", "SnapToDefaultButton", "0", 1, False, False),
    ("mouse_swap_off", "Основная кнопка мыши — левая", "Возвращает левую кнопку основной; применяй только если это твоё предпочтение.", "SwapMouseButtons", "0", 1, False, True),
    ("mouse_double_click", "Настроить двойной щелчок мыши", "Стандартный временной интервал двойного щелчка; меняется на 500 мс.", "DoubleClickSpeed", "500", 1, False, False),
])
_add_group("Клавиатура и ввод", r"Control Panel\Keyboard", [
    ("keyboard_delay", "Задержка повтора клавиш", "Настройка системной задержки повтора; не меняет задержку ввода в игре напрямую.", "KeyboardDelay", "1", 1, False, True),
    ("keyboard_speed", "Скорость повтора клавиш", "Устанавливает высокую системную скорость повтора клавиш.", "KeyboardSpeed", "31", 1, False, True),
])
_add_group("Проводник и панель задач", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", [
    ("explorer_file_ext", "Показывать расширения файлов", "Помогает отличать реальные типы файлов; это настройка удобства.", "HideFileExt", 0, 4, False, False),
    ("explorer_hidden_files", "Показывать скрытые файлы", "Показывает скрытые файлы и папки. Будь внимателен при ручной очистке.", "Hidden", 1, 4, False, True),
    ("explorer_superhidden_off", "Не показывать защищённые системные файлы", "Оставляет защищённые системные файлы скрытыми.", "ShowSuperHidden", 0, 4, True, False),
    ("explorer_launch_pc", "Открывать Проводник в «Этот компьютер»", "Удобнее для быстрого доступа к дискам.", "LaunchTo", 1, 4, False, False),
    ("explorer_separate_process", "Запускать папки в отдельном процессе", "Может повысить изоляцию окон Проводника, но использует немного больше памяти.", "SeparateProcess", 1, 4, False, True),
    ("explorer_status_bar", "Показывать строку состояния Проводника", "Показывает дополнительную информацию внизу окна Проводника.", "ShowStatusBar", 1, 4, False, False),
    ("explorer_autocheck_select", "Отключить флажки выбора файлов", "Меняет способ выбора файлов в Проводнике.", "AutoCheckSelect", 0, 4, False, False),
    ("explorer_expand_nav", "Раскрывать текущую папку в дереве", "Автоматически раскрывает дерево папок до текущего расположения.", "NavPaneExpandToCurrentFolder", 1, 4, False, False),
    ("explorer_all_folders", "Не показывать все папки в панели навигации", "Упрощает дерево навигации Проводника.", "NavPaneShowAllFolders", 0, 4, False, False),
    ("explorer_sync_toasts", "Скрыть подсказки поставщиков синхронизации", "Убирает рекламные подсказки OneDrive и других sync-провайдеров в Проводнике.", "ShowSyncProviderNotifications", 0, 4, False, False),
    ("explorer_frequent", "Не показывать частые папки в быстром доступе", "Уменьшает историю папок в быстром доступе.", "ShowFrequent", 0, 4, False, False),
    ("explorer_recent", "Не показывать последние файлы в быстром доступе", "Скрывает историю недавних файлов в Проводнике.", "ShowRecent", 0, 4, False, False),
    ("explorer_list_alpha", "Отключить прозрачное выделение файлов", "Меняет внешний вид выделения объектов в Проводнике.", "ListviewAlphaSelect", 0, 4, False, False),
    ("explorer_list_shadow", "Отключить тень названий значков", "Меняет отображение подписей значков рабочего стола.", "ListviewShadow", 0, 4, False, False),
    ("explorer_icons_only", "Показывать только значки панели задач", "Убирает подписи окон рядом со значками; на новых версиях Windows может игнорироваться.", "IconsOnly", 1, 4, False, True),
    ("explorer_disable_preview_desktop", "Отключить Aero Peek на рабочем столе", "Меняет поведение предварительного просмотра рабочего стола.", "DisablePreviewDesktop", 1, 4, False, False),
    ("taskbar_hide_view", "Скрыть кнопку представления задач", "Убирает кнопку Task View с панели задач.", "ShowTaskViewButton", 0, 4, False, False),
    ("taskbar_hide_widgets", "Скрыть виджеты Windows", "Убирает кнопку виджетов с панели задач в поддерживаемых сборках.", "TaskbarDa", 0, 4, False, False),
    ("taskbar_hide_chat", "Скрыть кнопку Chat / Teams", "Убирает устаревающую кнопку чата в поддерживаемых версиях Windows.", "TaskbarMn", 0, 4, False, False),
    ("taskbar_hide_cortana", "Скрыть кнопку Cortana", "Убирает кнопку Cortana в сборках Windows, где она ещё присутствует.", "ShowCortanaButton", 0, 4, False, False),
    ("taskbar_no_animation", "Отключить анимацию панели задач", "Уменьшает декоративную анимацию панели задач.", "TaskbarAnimations", 0, 4, False, False),
    ("taskbar_combine_never", "Группировать кнопки панели задач", "Включает стандартную группировку кнопок; поддержка зависит от версии Windows.", "TaskbarGlomLevel", 0, 4, False, True),
    ("taskbar_small_icons", "Компактные значки панели задач", "На старых версиях Windows уменьшает значки; Windows 11 может игнорировать параметр.", "TaskbarSmallIcons", 1, 4, False, True),
    ("taskbar_show_seconds", "Показывать секунды в часах", "Показывает секунды в часах панели задач; слегка увеличивает обновление часов.", "ShowSecondsInSystemClock", 1, 4, False, True),
    ("start_no_track_docs", "Не отслеживать недавно открытые документы", "Снижает сохранение истории документов в меню Пуск.", "Start_TrackDocs", 0, 4, False, False),
    ("start_no_track_apps", "Не отслеживать часто используемые приложения", "Меняет персонализированный список приложений в меню Пуск.", "Start_TrackProgs", 0, 4, False, False),
    ("start_no_recommendations", "Скрыть рекомендации меню Пуск", "Убирает рекомендуемый контент на поддерживаемых сборках Windows.", "Start_IrisRecommendations", 0, 4, False, True),
    ("start_no_account_notice", "Скрыть уведомления аккаунта в Пуск", "Убирает часть рекомендаций, связанных с учётной записью.", "Start_AccountNotifications", 0, 4, False, False),
    ("explorer_startup_delay", "Убрать задержку автозапуска оболочки", "Удаляет стартовую задержку некоторых приложений оболочки; может влиять на нагрузку при входе.", "StartupDelayInMSec", 0, 4, False, True),
])
_add_group("Рекомендации и приватность", r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager", [
    ("privacy_system_suggestions", "Отключить системные рекомендации", "Скрывает часть предложений Windows в системных интерфейсах.", "SystemPaneSuggestionsEnabled", 0, 4, False, False),
    ("privacy_lock_content_1", "Отключить рекомендации на экране блокировки", "Отключает часть рекламных рекомендаций экрана блокировки.", "RotatingLockScreenEnabled", 0, 4, False, False),
    ("privacy_lock_content_2", "Отключить подсказки на экране блокировки", "Отключает оверлей-подсказки на экране блокировки.", "RotatingLockScreenOverlayEnabled", 0, 4, False, False),
    ("privacy_suggested_apps", "Отключить предложения приложений", "Убирает предложения приложений и части промо-контента меню Пуск.", "SubscribedContent-338388Enabled", 0, 4, False, False),
    ("privacy_windows_tips", "Отключить советы Windows", "Отключает часть рекомендаций и советов системы.", "SubscribedContent-338389Enabled", 0, 4, False, False),
    ("privacy_welcome_experience", "Отключить рекомендации после обновлений", "Скрывает приветственные рекомендации после обновлений Windows.", "SubscribedContent-353694Enabled", 0, 4, False, False),
    ("privacy_settings_suggestions", "Отключить предложения в настройках", "Скрывает рекомендации и промо-контент на страницах настроек.", "SubscribedContent-353696Enabled", 0, 4, False, False),
    ("privacy_soft_landing", "Отключить подсказки новых возможностей", "Скрывает подсказки о новых функциях Windows.", "SoftLandingEnabled", 0, 4, False, False),
    ("privacy_silent_apps", "Отключить тихую установку рекомендованных приложений", "Запрещает некоторые автоматические рекомендации приложений текущему профилю.", "SilentInstalledAppsEnabled", 0, 4, False, False),
    ("privacy_preinstalled_apps", "Отключить предложения предустановленных приложений", "Убирает рекомендации предустановленных приложений.", "PreInstalledAppsEnabled", 0, 4, False, False),
    ("privacy_oem_apps", "Отключить OEM-рекомендации приложений", "Убирает некоторые предложения производителя устройства.", "OemPreInstalledAppsEnabled", 0, 4, False, False),
    ("privacy_content_310093", "Отключить дополнительный рекомендуемый контент A", "Скрывает один из типов подписанного рекомендательного контента Windows.", "SubscribedContent-310093Enabled", 0, 4, False, False),
    ("privacy_content_338387", "Отключить дополнительный рекомендуемый контент B", "Скрывает один из типов подписанного рекомендательного контента Windows.", "SubscribedContent-338387Enabled", 0, 4, False, False),
])
_add_group("Рекомендации и приватность", r"Software\Microsoft\Windows\CurrentVersion\AdvertisingInfo", [
    ("privacy_advertising_id", "Отключить рекламный идентификатор", "Отключает рекламный ID для приложений текущего пользователя.", "Enabled", 0, 4, False, False),
])
_add_group("Рекомендации и приватность", r"Software\Microsoft\Windows\CurrentVersion\Privacy", [
    ("privacy_tailored_experiences", "Отключить персонализированные рекомендации", "Отключает персонализацию советов на основе диагностических данных, если поддерживается сборкой.", "TailoredExperiencesWithDiagnosticDataEnabled", 0, 4, False, False),
])
_add_group("Рекомендации и приватность", r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications", [
    ("privacy_background_apps", "Ограничить фоновые приложения", "Глобально ограничивает фоновую активность поддерживаемых приложений. Может задержать синхронизацию и уведомления.", "GlobalUserDisabled", 1, 4, False, True),
])
_add_group("Поиск и рекомендации", r"Software\Microsoft\Windows\CurrentVersion\Search", [
    ("search_bing_suggestions", "Отключить веб-подсказки Bing в поиске", "На поддерживаемых версиях уменьшает веб-рекомендации в меню поиска Windows.", "BingSearchEnabled", 0, 4, False, False),
    ("search_cortana_consent", "Отключить интеграцию Cortana в поиске", "Устаревший параметр; современные сборки Windows могут его игнорировать.", "CortanaConsent", 0, 4, False, True),
    ("search_history_device", "Отключить историю поиска устройства", "Отключает сохранение истории поиска текущего профиля, если поддерживается.", "IsDeviceSearchHistoryEnabled", 0, 4, False, False),
])
_add_group("Уведомления и фокус", r"Software\Microsoft\Windows\CurrentVersion\PushNotifications", [
    ("notifications_toasts_off", "Отключить всплывающие уведомления Windows", "Отключает toast-уведомления для пользователя; сообщения приложений могут стать незаметными.", "ToastEnabled", 0, 4, False, True),
])
_add_group("Оболочка и запуск", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize", [
    ("startup_wait_idle", "Не ждать завершения idle при старте приложений", "Управляет поведением старта приложений оболочки; эффект зависит от Windows.", "WaitForIdleState", 0, 4, False, True),
])
_add_group("Персонализация Windows", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", [
    ("personalize_transparency_off", "Отключить прозрачность Windows", "Может снизить часть эффектов оболочки, но отключит системную прозрачность.", "EnableTransparency", 0, 4, False, True),
    ("personalize_apps_dark", "Тёмный режим приложений Windows", "Переключает светлую/тёмную тему поддерживающих приложений.", "AppsUseLightTheme", 0, 4, False, False),
    ("personalize_system_dark", "Тёмная тема системных окон Windows", "Переключает светлую/тёмную тему системных интерфейсов.", "SystemUsesLightTheme", 0, 4, False, False),
])
_add_group("Специальные возможности", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", [
    ("accessibility_animations", "Отключить анимации интерфейса Windows", "Отключает дополнительную анимацию оболочки в поддерживаемых сборках.", "TaskbarAnimations", 0, 4, False, False),
])
_add_group("Оболочка и запуск", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", [
    ("taskbar_search_icon", "Показывать поиск компактным значком", "На совместимых сборках выбирает режим значка поиска на панели задач.", "SearchboxTaskbarMode", 1, 4, False, False),
    ("explorer_show_full_path", "Показывать полный путь в заголовке Проводника", "Параметр для совместимых версий оболочки; может игнорироваться Windows 11.", "FullPath", 1, 4, False, False),
    ("explorer_show_drive_letters", "Показывать буквы дисков перед именами", "Меняет формат отображения дисков в панели навигации.", "ShowDriveLettersFirst", 4, 4, False, False),
    ("explorer_show_protected_os", "Показывать защищённые файлы ОС", "Показывает защищённые системные файлы. Не рекомендуется для обычного использования.", "ShowSuperHidden", 1, 4, False, True),
])

# More independent user-shell preferences, avoiding duplicate registry value paths.
_add_group("Удобство оболочки", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", [
    ("shell_show_file_ops", "Показывать полный ход операций с файлами", "Включает стандартный подробный диалог копирования, если поддерживается.", "ShowInfoTip", 1, 4, False, False),
    ("shell_show_tooltips", "Показывать подсказки элементов", "Управляет подсказками оболочки; полезно при навигации, не влияет на FPS.", "ShowInfoTip", 1, 4, False, False),
])

# Additional reversible settings for Liquid Glass 3.0.
# All registry entries remain under HKEY_CURRENT_USER. Many improve preference/control,
# discoverability or privacy; they are not promised FPS gains and may be ignored by Windows.
_add_group("Захват и игровые функции", r"Software\Microsoft\Windows\CurrentVersion\GameDVR", [
    ("capture_audio_off", "Отключить запись игрового звука", "Выключает запись аудио средствами Game DVR; не отключает микрофон в других приложениях.", "AudioCaptureEnabled", 0, 4, False, True),
    ("capture_history_off", "Отключить запись последних игровых моментов", "Выключает фоновый буфер Game DVR, если он поддерживается вашей сборкой Windows.", "HistoricalCaptureEnabled", 0, 4, False, True),
    ("capture_cursor_off", "Не записывать курсор в клипах", "Убирает системный указатель из записываемых клипов там, где параметр поддерживается.", "CursorCaptureEnabled", 0, 4, False, False),
    ("capture_microphone_off", "Отключить запись микрофона в Game DVR", "Меняет только захват микрофона функцией Game DVR; голосовые приложения не затрагиваются.", "MicrophoneCaptureEnabled", 0, 4, False, True),
])
_add_group("Мышь и ввод", r"Control Panel\Mouse", [
    ("mouse_sensitivity_default", "Стандартная чувствительность указателя", "Устанавливает стандартное значение указателя Windows. Не меняет чувствительность внутри игр; сохрани своё значение, если привык к нему.", "MouseSensitivity", "10", 1, False, True),
    ("mouse_hover_height", "Стандартная высота зоны наведения", "Возвращает типовой размер зоны наведения для системных элементов.", "MouseHoverHeight", "4", 1, False, False),
    ("mouse_hover_width", "Стандартная ширина зоны наведения", "Возвращает типовой размер зоны наведения для системных элементов.", "MouseHoverWidth", "4", 1, False, False),
    ("mouse_double_height", "Стандартная высота двойного щелчка", "Задаёт типовой размер зоны двойного щелчка мыши.", "DoubleClickHeight", "4", 1, False, False),
    ("mouse_double_width", "Стандартная ширина двойного щелчка", "Задаёт типовой размер зоны двойного щелчка мыши.", "DoubleClickWidth", "4", 1, False, False),
])
_add_group("Клавиатура и ввод", r"Control Panel\Keyboard", [
    ("keyboard_numlock_on", "Включать Num Lock при входе", "Просит Windows включать цифровой блок при входе; поведение зависит от прошивки и сборки.", "InitialKeyboardIndicators", "2", 1, False, False),
])
_add_group("Проводник и панель задач", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced", [
    ("taskbar_small_size", "Маленький размер панели задач", "На поддерживаемых версиях Windows 11 выбирает компактный размер панели задач.", "TaskbarSi", 0, 4, False, True),
    ("taskbar_left_align", "Выравнивать панель задач слева", "Меняет выравнивание панели задач в Windows 11 на поддерживаемых сборках.", "TaskbarAl", 0, 4, False, False),
    ("taskbar_hide_copilot", "Скрыть кнопку Copilot", "Убирает кнопку Copilot с панели задач в версиях Windows, где этот параметр поддерживается.", "ShowCopilotButton", 0, 4, False, False),
    ("taskbar_badges_off", "Скрыть счётчики уведомлений на значках", "Отключает бейджи уведомлений на значках панели задач, если поддерживается оболочкой.", "TaskbarBadges", 0, 4, False, False),
    ("explorer_compact_mode", "Компактный режим Проводника", "Уменьшает вертикальные отступы в поддерживаемых версиях Проводника.", "UseCompactMode", 1, 4, False, False),
    ("explorer_preview_handlers", "Включить обработчики предпросмотра", "Разрешает поддерживаемые обработчики предпросмотра в области просмотра Проводника.", "ShowPreviewHandlers", 1, 4, False, False),
    ("explorer_show_empty_drives", "Показывать пустые диски", "Возвращает отображение съёмных дисков без носителя в окне «Этот компьютер».", "HideDrivesWithNoMedia", 0, 4, False, False),
    ("explorer_compressed_color_off", "Обычный цвет сжатых файлов", "Убирает цветовое выделение NTFS-сжатых файлов в Проводнике.", "ShowEncryptCompressedColor", 0, 4, False, False),
    ("desktop_icons_visible", "Показывать значки рабочего стола", "Возвращает видимость значков рабочего стола, если ранее их скрыли через системную настройку.", "HideIcons", 0, 4, False, False),
    ("explorer_type_overlay", "Показывать значки типов файлов", "Включает отображение системных наложений типов файлов, если поддерживается оболочкой.", "ShowTypeOverlay", 1, 4, False, False),
    ("explorer_folder_tips", "Показывать сведения о папках", "Разрешает краткие подсказки содержимого папки при наведении в Проводнике.", "FolderContentsInfoTip", 1, 4, False, False),
])
_add_group("Поиск и рекомендации", r"Software\Microsoft\Windows\CurrentVersion\SearchSettings", [
    ("search_dynamic_box_off", "Скрыть динамические подсказки поиска", "Выключает динамические рекомендации в поле поиска на совместимых сборках Windows.", "IsDynamicSearchBoxEnabled", 0, 4, False, False),
    ("search_msa_cloud_off", "Отключить облачный поиск для личной учётной записи", "Ограничивает облачные результаты поиска, если параметр поддерживается этой версией Windows.", "IsMSACloudSearchEnabled", 0, 4, False, True),
    ("search_aad_cloud_off", "Отключить облачный поиск рабочей учётной записи", "Меняет облачный поиск для рабочей/учебной учётной записи; не включай, если полагаешься на него.", "IsAADCloudSearchEnabled", 0, 4, False, True),
])
_add_group("Буфер обмена", r"Software\Microsoft\Clipboard", [
    ("clipboard_history_off", "Отключить историю буфера обмена", "Не сохраняет список недавно скопированных элементов в истории Windows.", "EnableClipboardHistory", 0, 4, False, False),
    ("clipboard_cloud_upload_off", "Отключить отправку буфера обмена в облако", "Отключает автоматическую отправку содержимого буфера обмена в облачную синхронизацию, если поддерживается.", "CloudClipboardAutomaticUpload", 0, 4, False, True),
    ("clipboard_cloud_download_off", "Отключить получение буфера из облака", "Отключает автоматическое получение буфера обмена с других устройств, если поддерживается.", "CloudClipboardAutomaticDownload", 0, 4, False, True),
])
_add_group("Уведомления и фокус", r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings", [
    ("notifications_global_toasts_off", "Отключить всплывающие уведомления глобально", "Глобальная настройка toast-уведомлений. Сообщения приложений могут стать незаметными.", "NOC_GLOBAL_SETTING_TOASTS_ENABLED", 0, 4, False, True),
    ("notifications_sound_off", "Отключить звуки уведомлений", "Отключает системные звуковые сигналы уведомлений там, где поддерживается.", "NOC_GLOBAL_SETTING_ALLOW_NOTIFICATION_SOUND", 0, 4, False, False),
])
_add_group("Автозаполнение и Проводник", r"Software\Microsoft\Windows\CurrentVersion\Explorer\AutoComplete", [
    ("autocomplete_suggestions_off", "Отключить подсказки автозаполнения", "Отключает сохранённые подсказки в совместимых системных диалогах и адресных строках.", "AutoSuggest", "no", 1, False, False),
    ("autocomplete_append_on", "Дополнять ввод автозаполнением", "Включает автоматическое дополнение ввода в поддерживаемых системных полях.", "Append Completion", "yes", 1, False, False),
])
_add_group("Удобство Проводника", r"Software\Microsoft\Windows\CurrentVersion\Explorer\CabinetState", [
    ("explorer_full_path_title", "Показывать полный путь в окне Проводника", "Показывает полный путь в заголовке на версиях Проводника, где параметр используется.", "FullPath", 1, 4, False, False),
    ("explorer_legacy_search_assistant_off", "Отключить устаревший помощник поиска", "Убирает интеграцию старого Search Assistant в совместимых системных диалогах.", "Use Search Asst", "no", 1, False, False),
])
_add_group("Политики пользовательского профиля", r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer", [
    ("policy_no_recent_history", "Не вести историю последних документов", "Ограничивает список недавних документов в интерфейсах Windows текущего пользователя.", "NoRecentDocsHistory", 1, 4, False, False),
    ("policy_no_recent_menu", "Скрыть меню недавних документов", "Скрывает элементы недавних документов в поддерживаемых меню Windows.", "NoRecentDocsMenu", 1, 4, False, False),
    ("policy_clear_recent_on_exit", "Очищать историю документов при выходе", "Очищает историю документов текущего пользователя при выходе, если политика поддерживается.", "ClearRecentDocsOnExit", 1, 4, False, True),
    ("policy_autorun_off", "Отключить автозапуск съёмных носителей", "Ограничивает автоматическое выполнение действий при подключении носителей. Не отключает сам диск.", "NoDriveTypeAutoRun", 255, 4, False, False),
])
_add_group("Персонализация Windows", r"Software\Microsoft\Windows\DWM", [
    ("dwm_aero_peek_off", "Отключить Aero Peek", "Меняет поведение предварительного просмотра окон на совместимых версиях Windows.", "EnableAeroPeek", 0, 4, False, False),
    ("dwm_color_prevalence_off", "Не окрашивать рамки окон акцентным цветом", "Отключает использование акцентного цвета в системных заголовках окон на совместимых версиях.", "ColorPrevalence", 0, 4, False, False),
    ("dwm_inactive_color_default", "Стандартный цвет неактивных окон", "Возвращает нейтральный цвет для неактивных заголовков, если параметр поддерживается.", "AccentColorInactive", 0xFF444444, 4, False, False),
])
_add_group("Автозапуск и удобство", r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced\TaskbarDeveloperSettings", [
    ("taskbar_end_task_option", "Добавить «Завершить задачу» в панель задач", "Включает пункт завершения приложения из меню панели задач в поддерживаемых сборках Windows 11.", "TaskbarEndTask", 1, 4, False, False),
])
_add_group("Автозапуск и удобство", r"Software\Microsoft\Windows\CurrentVersion\Explorer\AutoplayHandlers", [
    ("autoplay_handlers_off", "Отключить обработчики автозапуска", "Ограничивает автоматические действия для подключаемых носителей текущего пользователя; при необходимости верни настройку.", "DisableAutoplay", 1, 4, False, True),
])
_add_group("Дополнительные рекомендации Windows", r"Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager", [
    ("privacy_content_310091", "Отключить дополнительный канал рекомендаций 310091", "Выключает отдельный канал рекомендательного контента на версиях Windows, где он используется.", "SubscribedContent-310091Enabled", 0, 4, False, False),
    ("privacy_content_310092", "Отключить дополнительный канал рекомендаций 310092", "Выключает отдельный канал рекомендательного контента на версиях Windows, где он используется.", "SubscribedContent-310092Enabled", 0, 4, False, False),
    ("privacy_content_310094", "Отключить дополнительный канал рекомендаций 310094", "Выключает отдельный канал рекомендательного контента на версиях Windows, где он используется.", "SubscribedContent-310094Enabled", 0, 4, False, False),
    ("privacy_content_338391", "Отключить дополнительный канал рекомендаций 338391", "Выключает отдельный канал рекомендательного контента на версиях Windows, где он используется.", "SubscribedContent-338391Enabled", 0, 4, False, False),
    ("privacy_content_338392", "Отключить дополнительный канал рекомендаций 338392", "Выключает отдельный канал рекомендательного контента на версиях Windows, где он используется.", "SubscribedContent-338392Enabled", 0, 4, False, False),
    ("privacy_content_338393", "Отключить дополнительный канал рекомендаций 338393", "Выключает отдельный канал рекомендательного контента на версиях Windows, где он используется.", "SubscribedContent-338393Enabled", 0, 4, False, False),
    ("privacy_content_338394", "Отключить дополнительный канал рекомендаций 338394", "Выключает отдельный канал рекомендательного контента на версиях Windows, где он используется.", "SubscribedContent-338394Enabled", 0, 4, False, False),
    ("privacy_content_353698", "Отключить дополнительный канал рекомендаций 353698", "Выключает отдельный канал рекомендательного контента на версиях Windows, где он используется.", "SubscribedContent-353698Enabled", 0, 4, False, False),
    ("privacy_content_353699", "Отключить дополнительный канал рекомендаций 353699", "Выключает отдельный канал рекомендательного контента на версиях Windows, где он используется.", "SubscribedContent-353699Enabled", 0, 4, False, False),
    ("privacy_content_353700", "Отключить дополнительный канал рекомендаций 353700", "Выключает отдельный канал рекомендательного контента на версиях Windows, где он используется.", "SubscribedContent-353700Enabled", 0, 4, False, False),
])

# Deduplicate by actual registry address: keep the first definition, and avoid duplicate
# aliases making two switches fight over the same value.
_seen_registry = set()
_clean_catalog: list[dict[str, Any]] = []
for _item in TWEAK_CATALOG:
    _rid = _item["id"]
    _tuples = REGISTRY_TWEAKS.get(_rid, [])
    if _tuples:
        _keys = {(p.lower(), n.lower()) for p, n, _, _ in _tuples}
        if any(k in _seen_registry for k in _keys):
            REGISTRY_TWEAKS.pop(_rid, None)
            continue
        _seen_registry.update(_keys)
    _clean_catalog.append(_item)
TWEAK_CATALOG = _clean_catalog

THEMES: dict[str, dict[str, str]] = {
    "liquid_glass": {"label": "Liquid Glass · Стекло", "accent": "#b7a4ff"},
    "black": {"label": "Чёрная", "accent": "#aaa3bb"},
}


CSS = r"""
* { font-family: 'Segoe UI', 'Inter', sans-serif; }
QMainWindow { background: #08070e; }
QWidget#root { background: transparent; color: #f7f4ff; }
QWidget { color: #f5f1ff; font-size: 13px; selection-background-color: #8b69f5; selection-color: #ffffff; }
QFrame#sidebar { background: rgba(15,12,24,174); border-right: 1px solid rgba(255,255,255,25); }
QFrame#topbar { background: rgba(17,13,28,156); border-bottom: 1px solid rgba(255,255,255,25); }
QFrame#card { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(255,255,255,22), stop:0.18 rgba(70,54,98,148), stop:0.6 rgba(30,24,45,180), stop:1 rgba(20,16,31,194)); border: 1px solid rgba(255,255,255,34); border-top: 1px solid rgba(255,255,255,96); border-radius: 20px; }
QFrame#cardAlt { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(177,143,255,42), stop:0.22 rgba(78,54,111,159), stop:1 rgba(23,18,36,193)); border: 1px solid rgba(211,190,255,80); border-top: 1px solid rgba(255,255,255,125); border-radius: 21px; }
QFrame#hero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(154,119,255,100), stop:0.2 rgba(74,52,111,183), stop:0.65 rgba(31,24,48,202), stop:1 rgba(19,16,30,210)); border: 1px solid rgba(220,202,255,125); border-top: 1px solid rgba(255,255,255,175); border-radius: 26px; }
QLabel#muted { color: #b1a8c6; }
QLabel#eyebrow { color: #d2c1ff; font-size: 10px; font-weight: 800; letter-spacing: 1.35px; }
QLabel#bigNumber { color: #ffffff; font-size: 29px; font-weight: 800; }
QLabel#pageTitle { color: #fbfaff; font-size: 29px; font-weight: 800; }
QLabel#sectionTitle { color: #f6f1ff; font-size: 16px; font-weight: 750; }
QLabel#smallCaps { color: #b8abc9; font-size: 10px; font-weight: 800; letter-spacing: 1.2px; }
QPushButton { border: 1px solid rgba(255,255,255,48); border-top: 1px solid rgba(255,255,255,142); border-radius: 14px; background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 rgba(255,255,255,21), stop:0.45 rgba(255,255,255,9), stop:1 rgba(255,255,255,4)); padding: 11px 15px; color: #f7f2ff; font-weight: 650; }
QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 rgba(221,203,255,54), stop:1 rgba(127,91,196,28)); border-color: rgba(236,222,255,198); }
QPushButton:pressed { background: rgba(137,102,218,120); border-color: rgba(255,255,255,220); padding-top: 12px; padding-bottom: 10px; }
QPushButton:disabled { color: #716983; background: rgba(35,30,45,92); border-color: rgba(255,255,255,18); }
QPushButton#primary { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 rgba(195,171,255,245), stop:0.28 rgba(147,111,255,238), stop:0.7 rgba(116,77,220,240), stop:1 rgba(82,53,155,242)); border: 1px solid rgba(255,255,255,205); border-top: 1px solid #ffffff; color: white; font-weight: 800; }
QPushButton#primary:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #d5c4ff, stop:0.45 #a98aff, stop:1 #8960eb); }
QPushButton#primary:pressed { background: #704bc4; }
QPushButton#ghost { background: rgba(255,255,255,8); border-color: rgba(255,255,255,44); }
QPushButton#nav { background: transparent; color: #b7aecb; border: 1px solid transparent; border-radius: 14px; text-align: left; padding: 13px 13px; font-weight: 650; }
QPushButton#nav:hover { background: rgba(255,255,255,13); color: #ffffff; border-color: rgba(255,255,255,34); }
QPushButton#nav[active="true"] { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(185,152,255,70), stop:0.5 rgba(139,92,246,32), stop:1 rgba(255,255,255,8)); color: #ffffff; border: 1px solid rgba(224,207,255,110); }
QPushButton#tiny { padding: 7px 11px; border-radius: 11px; font-size: 11px; }
QLineEdit, QComboBox { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 rgba(255,255,255,16), stop:1 rgba(255,255,255,5)); border: 1px solid rgba(255,255,255,45); border-top: 1px solid rgba(255,255,255,115); border-radius: 14px; padding: 10px 13px; color: #f7f2ff; min-height: 18px; selection-background-color: #6f48c0; }
QLineEdit:focus, QComboBox:focus { border-color: rgba(210,187,255,230); background: rgba(154,119,255,24); }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView { background: #21182f; color: #f7f4ff; selection-background-color: #6542a4; border: 1px solid rgba(255,255,255,65); outline: none; padding: 5px; }
QCheckBox { spacing: 10px; color: #f3edff; font-weight: 650; }
QCheckBox::indicator { width: 42px; height: 24px; border-radius: 12px; background: rgba(255,255,255,17); border: 1px solid rgba(255,255,255,70); }
QCheckBox::indicator:checked { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #c2aaff, stop:0.45 #9a72ff, stop:1 #7045d3); border-color: #f2eaff; }
QCheckBox::indicator:unchecked:hover { border-color: rgba(233,219,255,200); background: rgba(255,255,255,28); }
QProgressBar { height: 7px; border: 1px solid rgba(255,255,255,18); border-radius: 4px; background: rgba(255,255,255,10); text-align: center; }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #d3c3ff, stop:0.45 #9b78ff, stop:1 #7650db); border-radius: 4px; }
QScrollArea, QAbstractScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 5px 1px; }
QScrollBar::handle:vertical { background: rgba(225,211,255,76); min-height: 28px; border-radius: 4px; }
QScrollBar::handle:vertical:hover { background: rgba(242,232,255,150); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QFrame#card[selectedGame="true"], QFrame#cardAlt[selectedGame="true"] { border: 1px solid rgba(255,255,255,165); border-top: 1px solid rgba(255,255,255,235); }
QFrame#glassDivider { background: rgba(255,255,255,42); max-height: 1px; }
"""



def app_data_dir() -> Path:
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    else:
        base = Path.home() / ".local" / "share"
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


class Store:
    def __init__(self) -> None:
        self.path = app_data_dir() / "settings.json"
        self.data: dict[str, Any] = {"theme": "liquid_glass", "selected_game": "fortnite", "active_tweaks": [], "registry_backup": {}, "power_backup": None, "ui_version": 5}
        try:
            if self.path.exists():
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self.data.update(loaded)
                    # One-time migration: use the requested Liquid Glass design on upgrade,
                    # then preserve later theme choices across future launches.
                    # Migrate the retired purple-glass theme without resetting any other
                    # existing user's preferred black/green/blue theme.
                    was_retired_theme = self.data.get("theme") == "purple_glass"
                    if was_retired_theme:
                        self.data["theme"] = "liquid_glass"
                    # Remove legacy regional matchmaking preferences: ForGo is now a general game optimizer.
                    self.data.pop("region", None)
                    self.data.pop("matchmaking_region", None)
                    if int(loaded.get("ui_version", 0) or 0) < 5 or was_retired_theme:
                        self.data["ui_version"] = 5
                        self.save()
        except Exception:
            pass

    def save(self) -> None:
        try:
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.path)
        except Exception:
            pass

    def active(self, name: str) -> bool:
        return name in self.data.get("active_tweaks", [])

    def set_active(self, name: str, active: bool) -> None:
        current = set(self.data.get("active_tweaks", []))
        current.add(name) if active else current.discard(name)
        self.data["active_tweaks"] = sorted(current)
        self.save()


class Optimizer:
    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def _windows_registry():
        if not IS_WINDOWS:
            raise RuntimeError("Твики Windows доступны только в Windows 10/11.")
        import winreg
        return winreg

    def apply_registry_tweak(self, tweak_id: str, enabled: bool) -> None:
        winreg = self._windows_registry()
        if tweak_id not in REGISTRY_TWEAKS:
            raise ValueError("Неизвестный твик")
        backup = self.store.data.setdefault("registry_backup", {})
        for path, value_name, value, reg_type in REGISTRY_TWEAKS[tweak_id]:
            key_id = f"{path}|{value_name}"
            if enabled:
                if key_id not in backup:
                    try:
                        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
                            old_value, old_type = winreg.QueryValueEx(key, value_name)
                        backup[key_id] = {"exists": True, "value": old_value, "type": old_type}
                    except FileNotFoundError:
                        backup[key_id] = {"exists": False}
                    except OSError:
                        backup[key_id] = {"exists": False}
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, value_name, 0, reg_type, value)
            else:
                saved = backup.get(key_id)
                if saved is not None:
                    try:
                        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                            if saved.get("exists"):
                                winreg.SetValueEx(key, value_name, 0, saved.get("type", reg_type), saved.get("value"))
                            else:
                                try:
                                    winreg.DeleteValue(key, value_name)
                                except FileNotFoundError:
                                    pass
                    except OSError as exc:
                        raise RuntimeError(f"Не удалось восстановить параметр {value_name}: {exc}") from exc
                    backup.pop(key_id, None)
        self.store.save()
        self.store.set_active(tweak_id, enabled)
        if tweak_id == "mouse_accel":
            self._notify_mouse_settings()

    @staticmethod
    def _notify_mouse_settings() -> None:
        if not IS_WINDOWS:
            return
        try:
            import winreg
            values = []
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Mouse", 0, winreg.KEY_READ) as key:
                for name, default in (("MouseThreshold1", "6"), ("MouseThreshold2", "10"), ("MouseSpeed", "1")):
                    try: value, _ = winreg.QueryValueEx(key, name)
                    except OSError: value = default
                    values.append(int(value))
            params = (ctypes.c_int * 3)(*values)
            # SPI_SETMOUSE = 0x0004, SPIF_SENDCHANGE = 0x0002
            ctypes.windll.user32.SystemParametersInfoW(0x0004, 0, ctypes.byref(params), 0x0002)
        except Exception:
            # The registry backup is still preserved; the setting may take effect after sign-out.
            pass

    def set_power_plan(self, enabled: bool) -> None:
        if not IS_WINDOWS:
            raise RuntimeError("Профиль питания доступен только в Windows.")
        def run(args: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.run(args, capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if enabled:
            if not self.store.data.get("power_backup"):
                result = run(["powercfg", "/getactivescheme"])
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or "Не удалось прочитать текущий план питания.")
                match = re.search(r"([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})", result.stdout)
                if match:
                    self.store.data["power_backup"] = match.group(1)
                    self.store.save()
            result = run(["powercfg", "/setactive", "SCHEME_MIN"])
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "Windows не смогла включить план высокой производительности.")
        else:
            original = self.store.data.get("power_backup")
            if original:
                result = run(["powercfg", "/setactive", original])
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or "Не удалось восстановить прежний план питания.")
            self.store.data["power_backup"] = None
            self.store.save()
        self.store.set_active("power_plan", enabled)

    def set_tweak(self, tweak_id: str, enabled: bool) -> None:
        if tweak_id in REGISTRY_TWEAKS:
            self.apply_registry_tweak(tweak_id, enabled)
        elif tweak_id == "power_plan":
            self.set_power_plan(enabled)
        else:
            raise ValueError("Неизвестный твик")

    def restore_all(self) -> list[str]:
        problems: list[str] = []
        for tweak_id in [x for x in self.store.data.get("active_tweaks", []) if x in REGISTRY_TWEAKS or x == "power_plan"]:
            if self.store.active(tweak_id):
                try:
                    self.set_tweak(tweak_id, False)
                except Exception as exc:
                    problems.append(f"{tweak_id}: {exc}")
        # Recover also from an interrupted run where a backup was saved but the active flag wasn't.
        if self.store.data.get("registry_backup"):
            try:
                winreg = self._windows_registry()
                for key_id, saved in list(self.store.data["registry_backup"].items()):
                    try:
                        path, value_name = key_id.rsplit("|", 1)
                        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                            if saved.get("exists"):
                                winreg.SetValueEx(key, value_name, 0, saved.get("type", winreg.REG_DWORD), saved.get("value"))
                            else:
                                try: winreg.DeleteValue(key, value_name)
                                except FileNotFoundError: pass
                        self.store.data["registry_backup"].pop(key_id, None)
                    except Exception as exc:
                        problems.append(f"{key_id}: {exc}")
                self.store.save()
            except Exception as exc:
                problems.append(str(exc))
        if self.store.data.get("power_backup") and not self.store.active("power_plan"):
            try:
                self.set_power_plan(False)
            except Exception as exc:
                problems.append(f"power plan: {exc}")
        return problems


def _epic_launcher_candidates() -> list[Path]:
    """Find Epic Games Launcher in common folders and Windows registry locations."""
    found: list[Path] = []

    def add_exe(path: Path | str) -> None:
        try:
            candidate = Path(os.path.expandvars(str(path).strip().strip('"')))
            if candidate.is_dir():
                candidate = candidate / "Portal" / "Binaries" / "Win64" / "EpicGamesLauncher.exe"
            if candidate.is_file() and candidate.name.lower() == "epicgameslauncher.exe":
                resolved = candidate.resolve()
                if resolved not in found:
                    found.append(resolved)
        except (OSError, ValueError):
            pass

    # Common installs and per-user installs.
    roots = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "Programs",
    ]
    for root in roots:
        add_exe(root / "Epic Games" / "Launcher" / "Portal" / "Binaries" / "Win64" / "EpicGamesLauncher.exe")
        add_exe(root / "Epic Games Launcher" / "Portal" / "Binaries" / "Win64" / "EpicGamesLauncher.exe")

    # Read install locations and the registered protocol handler. This covers custom install paths.
    try:
        import winreg  # type: ignore[import-not-found]
        registry_locations = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Epic Games\EpicGamesLauncher"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Epic Games\EpicGamesLauncher"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Epic Games Launcher"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Epic Games Launcher"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Epic Games Launcher"),
        ]
        for hive, key_path in registry_locations:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    for value_name in ("InstallLocation", "InstallDir", "Path", "DisplayIcon"):
                        try:
                            value = winreg.QueryValueEx(key, value_name)[0]
                        except OSError:
                            continue
                        if isinstance(value, str) and value.strip():
                            raw = value.strip().strip('"')
                            # DisplayIcon sometimes ends in a comma and an icon index.
                            raw = re.sub(r",\s*-?\d+\s*$", "", raw)
                            path = Path(os.path.expandvars(raw))
                            add_exe(path)
                            add_exe(path / "Portal" / "Binaries" / "Win64" / "EpicGamesLauncher.exe")
            except OSError:
                continue

        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"com.epicgames.launcher\shell\open\command") as key:
                command = winreg.QueryValueEx(key, None)[0]
            if isinstance(command, str):
                match = re.search(r'"([^\"]*EpicGamesLauncher\.exe)"|([^\s]*EpicGamesLauncher\.exe)', command, re.IGNORECASE)
                if match:
                    add_exe(match.group(1) or match.group(2))
        except OSError:
            pass
    except ImportError:
        pass

    return found


def _write_launch_log(lines: list[str], game_name: str = "Fortnite") -> str | None:
    """Save launch diagnostics so a failed launch can be diagnosed without guessing."""
    try:
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "ForGo"
        base.mkdir(parents=True, exist_ok=True)
        log_path = base / "launch.log"
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {game_name} launch attempt\n")
            stream.write("\n".join(lines) + "\n")
        return str(log_path)
    except OSError:
        return None


def launch_fortnite() -> tuple[bool, str]:
    """Launch Fortnite via Epic's documented protocol, never bypassing its anti-cheat."""
    if not IS_WINDOWS:
        return False, "Запуск Fortnite из ForGo поддерживается только в Windows."

    # Epic's documented product URI for Fortnite.
    uri = "com.epicgames.launcher://apps/fn%3A4fe75bbc5a674f4f9b356b5c90567da5%3AFortnite?action=launch&silent=true"
    # Some launcher versions create the desktop shortcut with this shorter URI.
    fallback_uri = "com.epicgames.launcher://apps/Fortnite?action=launch&silent=true"
    diagnostics: list[str] = [f"Primary URI: {uri}"]

    # Avoid unnecessarily requesting launch when Fortnite is already running.
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name in {"fortniteclient-win64-shipping.exe", "fortniteclient-win64-shipping_be.exe"}:
                    log_path = _write_launch_log(["Fortnite process already running."])
                    return True, "Fortnite уже запущен."
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception as exc:
        diagnostics.append(f"Process check: {exc!r}")

    launchers = _epic_launcher_candidates()
    diagnostics.append(f"Detected Epic launcher executables: {len(launchers)}")

    # Prefer launching the official Epic client directly with its protocol URI. The previous
    # implementation used os.startfile first; Windows could report success even when the URI
    # handler did not actually pass the request to Epic Games Launcher.
    for launcher in launchers:
        try:
            subprocess.Popen(
                [str(launcher), uri],
                cwd=str(launcher.parent),
                close_fds=False,
            )
            diagnostics.append(f"Started Epic launcher with URI: {launcher}")
            _write_launch_log(diagnostics)
            return True, "Команда запуска Fortnite передана Epic Games Launcher. Если игра не установлена, открой библиотеку Epic и проверь её установку."
        except (OSError, subprocess.SubprocessError) as exc:
            diagnostics.append(f"Direct launcher failed ({launcher}): {exc!r}")

    # Fallback to the Windows protocol association if the client path cannot be discovered.
    for candidate_uri in (uri, fallback_uri):
        try:
            os.startfile(candidate_uri)  # type: ignore[attr-defined]
            diagnostics.append(f"Windows protocol handler opened: {candidate_uri}")
            _write_launch_log(diagnostics)
            return True, "Команда запуска передана Windows. Если Fortnite не откроется, проверь Epic Games Launcher и его библиотеку."
        except Exception as exc:
            diagnostics.append(f"Protocol handler failed ({candidate_uri}): {exc!r}")

    # Last resort: open the Epic client itself instead of silently doing nothing.
    for launcher in launchers:
        try:
            subprocess.Popen([str(launcher)], cwd=str(launcher.parent), close_fds=False)
            diagnostics.append(f"Opened Epic launcher without game URI: {launcher}")
            _write_launch_log(diagnostics)
            return True, "Epic Games Launcher открыт. Найди Fortnite в библиотеке и нажми «Запустить»."
        except (OSError, subprocess.SubprocessError) as exc:
            diagnostics.append(f"Opening launcher failed ({launcher}): {exc!r}")

    log_path = _write_launch_log(diagnostics)
    extra = f"\n\nДиагностика сохранена: {log_path}" if log_path else ""
    return False, (
        "ForGo не смог найти или открыть Epic Games Launcher. Установи/восстанови Epic Games Launcher, "
        "выполни вход и убедись, что Fortnite установлена. Затем перезапусти ForGo." + extra
    )


def _steam_launcher_candidates() -> list[Path]:
    """Find the Steam desktop client without scanning entire drives."""
    found: list[Path] = []

    def add(path: Path | str) -> None:
        try:
            candidate = Path(os.path.expandvars(str(path).strip().strip('"')))
            if candidate.is_dir():
                candidate = candidate / "steam.exe"
            if candidate.is_file() and candidate.name.lower() == "steam.exe":
                candidate = candidate.resolve()
                if candidate not in found:
                    found.append(candidate)
        except (OSError, ValueError):
            pass

    roots = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Steam",
        Path.home() / "AppData" / "Local" / "Programs" / "Steam",
    ]
    for root in roots:
        add(root / "steam.exe")

    try:
        import winreg  # type: ignore[import-not-found]
        keys = [
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        ]
        for hive, key_path in keys:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    for value_name in ("SteamPath", "InstallPath"):
                        try:
                            value, _ = winreg.QueryValueEx(key, value_name)
                            if isinstance(value, str) and value.strip():
                                add(Path(os.path.expandvars(value)) / "steam.exe")
                                add(value)
                        except OSError:
                            continue
            except OSError:
                continue
    except ImportError:
        pass
    return found


def _riot_client_candidates() -> list[Path]:
    """Find Riot Client Services in common and registry-provided install locations."""
    found: list[Path] = []

    def add(path: Path | str) -> None:
        try:
            candidate = Path(os.path.expandvars(str(path).strip().strip('"')))
            if candidate.is_dir():
                candidate = candidate / "RiotClientServices.exe"
            if candidate.is_file() and candidate.name.lower() == "riotclientservices.exe":
                candidate = candidate.resolve()
                if candidate not in found:
                    found.append(candidate)
        except (OSError, ValueError):
            pass

    roots = [
        Path(r"C:\Riot Games\Riot Client"),
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Riot Games" / "Riot Client",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Riot Games" / "Riot Client",
    ]
    for root in roots:
        add(root / "RiotClientServices.exe")

    try:
        import winreg  # type: ignore[import-not-found]
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Riot Games,Riot Client"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Riot Games,Riot Client"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Riot Games,Riot Client"),
        ]
        for hive, key_path in keys:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    for value_name in ("InstallPath", "InstallLocation", "Path", "ExecutablePath"):
                        try:
                            value, _ = winreg.QueryValueEx(key, value_name)
                            if isinstance(value, str) and value.strip():
                                add(value)
                                add(Path(os.path.expandvars(value)) / "RiotClientServices.exe")
                        except OSError:
                            continue
            except OSError:
                continue
    except ImportError:
        pass
    return found


def launch_game(game_id: str) -> tuple[bool, str]:
    """Launch a supported title through its official launcher and keep anti-cheat intact."""
    if game_id not in GAME_CATALOG:
        return False, "Неизвестная игра. Выбери игру в библиотеке ForGo."
    if not IS_WINDOWS:
        return False, "Запуск игр из ForGo поддерживается только в Windows."

    game = GAME_CATALOG[game_id]
    name = game["name"]
    kind = game["kind"]
    diagnostics = [f"Selected game: {game_id} ({name})", f"Launcher kind: {kind}"]

    try:
        targets = {p.lower() for p in game.get("process_names", [])}
        for proc in psutil.process_iter(["name"]):
            try:
                proc_name = (proc.info.get("name") or "").lower()
                if proc_name in targets:
                    _write_launch_log([f"{name} process already running: {proc_name}"], name)
                    return True, f"{name} уже запущена."
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception as exc:
        diagnostics.append(f"Process check: {exc!r}")

    if kind == "epic":
        return launch_fortnite()

    if kind == "steam":
        appid = str(game["appid"])
        diagnostics.append(f"Steam app id: {appid}")
        launchers = _steam_launcher_candidates()
        diagnostics.append(f"Detected Steam clients: {len(launchers)}")
        for launcher in launchers:
            try:
                subprocess.Popen([str(launcher), "-applaunch", appid], cwd=str(launcher.parent), close_fds=False)
                _write_launch_log(diagnostics + [f"Started Steam with -applaunch: {launcher}"], name)
                return True, f"Команда запуска {name} передана Steam. Если игра не установлена, проверь её в библиотеке Steam."
            except (OSError, subprocess.SubprocessError) as exc:
                diagnostics.append(f"Steam executable failed ({launcher}): {exc!r}")
        uri = f"steam://run/{appid}"
        try:
            os.startfile(uri)  # type: ignore[attr-defined]
            _write_launch_log(diagnostics + [f"Opened Steam URI: {uri}"], name)
            return True, f"Команда запуска {name} передана Steam через Windows."
        except Exception as exc:
            diagnostics.append(f"Steam URI failed ({uri}): {exc!r}")
        log_path = _write_launch_log(diagnostics, name)
        extra = f"\n\nЛог: {log_path}" if log_path else ""
        return False, f"Не удалось открыть Steam для игры {name}. Установи Steam, выполни вход и проверь установку игры.{extra}"

    if kind == "riot":
        product = str(game["product"])
        launchers = _riot_client_candidates()
        diagnostics.append(f"Riot product: {product}")
        diagnostics.append(f"Detected Riot Client services: {len(launchers)}")
        for launcher in launchers:
            try:
                subprocess.Popen(
                    [str(launcher), f"--launch-product={product}", "--launch-patchline=live"],
                    cwd=str(launcher.parent), close_fds=False,
                )
                _write_launch_log(diagnostics + [f"Started Riot Client product via: {launcher}"], name)
                return True, f"Команда запуска {name} передана Riot Client. Если клиент попросит вход или обновление, заверши их и повтори запуск."
            except (OSError, subprocess.SubprocessError) as exc:
                diagnostics.append(f"Riot Client failed ({launcher}): {exc!r}")
        uri = f"riotclient://launch-product={product}&launch-patchline=live"
        try:
            os.startfile(uri)  # type: ignore[attr-defined]
            _write_launch_log(diagnostics + [f"Opened Riot URI: {uri}"], name)
            return True, f"Команда запуска {name} передана Riot Client через Windows."
        except Exception as exc:
            diagnostics.append(f"Riot URI failed ({uri}): {exc!r}")
        log_path = _write_launch_log(diagnostics, name)
        extra = f"\n\nЛог: {log_path}" if log_path else ""
        return False, f"Не удалось найти или запустить Riot Client. Установи/восстанови официальный клиент Riot и проверь установку {name}.{extra}"

    return False, f"Для {name} не настроен способ запуска."


class LiquidGlassRoot(QWidget):
    """Ambient backdrop for Liquid Glass. Native Windows acrylic is added by MainWindow."""
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = "liquid_glass"
        self._phase = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAutoFillBackground(False)
        self._ambient_timer = QTimer(self)
        self._ambient_timer.setInterval(55)  # subtle ~18 fps ambient motion, low overhead
        self._ambient_timer.timeout.connect(self._advance_ambient)
        self._ambient_timer.start()

    def _advance_ambient(self) -> None:
        if self._theme == "liquid_glass":
            self._phase = (self._phase + 0.009) % (2 * 3.141592653589793)
            self.update()

    def set_theme(self, theme_id: str) -> None:
        self._theme = theme_id if theme_id in THEMES else "liquid_glass"
        if self._theme == "liquid_glass":
            if not self._ambient_timer.isActive():
                self._ambient_timer.start()
        else:
            self._ambient_timer.stop()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        if rect.isEmpty():
            painter.end()
            return
        base = QLinearGradient(0, 0, rect.width(), rect.height())
        if self._theme == "liquid_glass":
            # Leave most of the top-level surface transparent so Windows acrylic can
            # blur the desktop behind it. UI panels add their own ~65% opaque tint.
            base.setColorAt(0.0, QColor(15, 11, 27, 28))
            base.setColorAt(0.48, QColor(17, 14, 31, 38))
            base.setColorAt(1.0, QColor(8, 8, 17, 34))
            painter.fillRect(rect, QBrush(base))
            import math
            phase = self._phase
            glows = [
                (0.79 + math.sin(phase) * 0.045, 0.13 + math.cos(phase * .8) * 0.035, (153, 126, 255)),
                (0.18 + math.cos(phase * .72) * 0.04, 0.84 + math.sin(phase * .9) * 0.035, (91, 126, 223)),
                (0.56 + math.sin(phase * .48) * 0.025, 0.5 + math.cos(phase * .6) * 0.025, (181, 157, 255)),
            ]
        elif self._theme == "black":
            base.setColorAt(0.0, QColor("#0c0b10")); base.setColorAt(1.0, QColor("#17141d"))
            painter.fillRect(rect, QBrush(base)); glows = [(0.84, 0.09, (145, 137, 164)), (0.16, 0.9, (76, 70, 91))]
        else:
            base.setColorAt(0.0, QColor("#090711")); base.setColorAt(1.0, QColor("#08070e"))
            painter.fillRect(rect, QBrush(base)); glows = [(0.84, 0.08, (155, 111, 255))]

        radius = max(rect.width(), rect.height()) * (0.58 if self._theme == "liquid_glass" else 0.72)
        for x, y, rgb in glows:
            gradient = QRadialGradient(QPointF(rect.width() * x, rect.height() * y), radius)
            alpha = 54 if self._theme == "liquid_glass" else 58
            gradient.setColorAt(0.0, QColor(rgb[0], rgb[1], rgb[2], alpha))
            gradient.setColorAt(0.38, QColor(rgb[0], rgb[1], rgb[2], 22 if self._theme == "liquid_glass" else 25))
            gradient.setColorAt(1.0, QColor(rgb[0], rgb[1], rgb[2], 0))
            painter.fillRect(rect, QBrush(gradient))
        # Fine top specular highlight that shifts gently with the ambient phase.
        sheen = 18 + int(8 * (0.5 + 0.5 * __import__('math').sin(self._phase))) if self._theme == "liquid_glass" else 13
        painter.setPen(QColor(255, 255, 255, sheen))
        painter.drawLine(0, 1, rect.width(), 1)
        if self._theme == "liquid_glass":
            painter.setPen(QColor(255, 255, 255, 12))
            painter.drawLine(1, 2, rect.width() - 1, 2)
        painter.end()


class GlassSwitch(QWidget):
    """Custom animated toggle with a glass track and a luminous moving thumb."""
    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._position = 1.0 if checked else 0.0
        self._accent = QColor("#b7a4ff")
        self._hovered = False
        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(210)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setFixedSize(54, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Включить или выключить твик")
        self.setAccessibleName("Переключатель твика")

    def getPosition(self) -> float:
        return self._position

    def setPosition(self, value: float) -> None:
        self._position = max(0.0, min(1.0, float(value)))
        self.update()

    position = Property(float, getPosition, setPosition)

    def setAccent(self, color: str) -> None:
        self._accent = QColor(color)
        self.update()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()
        if not self.signalsBlocked():
            self.toggled.emit(checked)
        self.setAccessibleDescription("Включено" if checked else "Выключено")

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.setChecked(not self._checked)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.setChecked(not self._checked)
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        from PySide6.QtCore import QRectF
        track = QRectF(1.0, 1.0, w - 2.0, h - 2.0)
        if self._checked:
            c = QColor(self._accent)
            c.setAlpha(215 if self._hovered else 190)
            fill = QLinearGradient(0, 0, w, h)
            top = QColor(c); top = top.lighter(128); top.setAlpha(c.alpha())
            bottom = QColor(c); bottom = bottom.darker(130); bottom.setAlpha(c.alpha())
            fill.setColorAt(0, top); fill.setColorAt(1, bottom)
            painter.setBrush(QBrush(fill))
            border = QColor(255, 255, 255, 180 if self._hovered else 122)
        else:
            painter.setBrush(QColor(255, 255, 255, 48 if self._hovered else 27))
            border = QColor(255, 255, 255, 135 if self._hovered else 72)
        painter.setPen(border)
        painter.drawRoundedRect(track, 15, 15)
        # Top-edge specular highlight.
        painter.setPen(QColor(255, 255, 255, 82 if self._checked else 64))
        painter.drawLine(8, 2, w - 8, 2)
        diameter = 22.0
        x = 4.0 + self._position * (w - diameter - 8.0)
        thumb = QRectF(x, 4.0, diameter, diameter)
        painter.setPen(QColor(255, 255, 255, 180))
        knob = QLinearGradient(thumb.topLeft(), thumb.bottomRight())
        knob.setColorAt(0, QColor(255, 255, 255, 255))
        knob.setColorAt(1, QColor(225, 228, 239, 242))
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.drawEllipse(thumb.translated(0, 1.2))
        painter.setBrush(QBrush(knob))
        painter.drawEllipse(thumb)
        painter.setPen(QColor(255, 255, 255, 165))
        painter.drawLine(int(x + 6), 8, int(x + diameter - 6), 8)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(self._accent.red(), self._accent.green(), self._accent.blue(), 220))
            painter.drawRoundedRect(track.adjusted(-1, -1, 1, 1), 16, 16)
        painter.end()


class Card(QFrame):
    def __init__(self, alt: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cardAlt" if alt else "card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


class NavButton(QPushButton):
    def __init__(self, glyph: str, text: str, page: str) -> None:
        super().__init__(f"  {glyph}     {text}")
        self.page_key = page
        self.setObjectName("nav")
        self.setProperty("active", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(44)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ForGo")
        self.resize(1320, 860)
        self.setMinimumSize(980, 660)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.store = Store()
        if self.store.data.get("selected_game") not in GAME_CATALOG:
            self.store.data["selected_game"] = "fortnite"
            self.store.save()
        self.optimizer = Optimizer(self.store)
        self.nav_buttons: dict[str, NavButton] = {}
        self.game_card_select_buttons: dict[str, QPushButton] = {}
        self.game_card_launch_buttons: dict[str, QPushButton] = {}
        self.game_card_widgets: dict[str, QFrame] = {}
        self.game_grid: QGridLayout | None = None
        self.priority_active = False
        self.original_priorities: dict[int, int] = {}
        self._drag_pos: QPoint | None = None
        self._build_ui()
        self._apply_theme(str(self.store.data.get("theme", "liquid_glass")))
        self._apply_nav("home")
        self._refresh_system()
        self.metrics_timer = QTimer(self)
        self.metrics_timer.timeout.connect(self._refresh_system)
        self.metrics_timer.start(2200)
        self.priority_timer = QTimer(self)
        self.priority_timer.timeout.connect(self._maintain_priority)
        self.priority_timer.start(3000)

    def _build_ui(self) -> None:
        outer = LiquidGlassRoot()
        self.liquid_root = outer
        outer.setObjectName("root")
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        self.setCentralWidget(outer)

        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(38)
        top = QHBoxLayout(topbar)
        top.setContentsMargins(15, 0, 10, 0)
        top.setSpacing(10)
        mini_logo = QLabel("FG")
        mini_logo.setFixedSize(24, 24)
        mini_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mini_logo.setStyleSheet("background:#8b5cf6;border-radius:7px;color:white;font-size:10px;font-weight:800;")
        brand = QLabel("ForGo")
        brand.setStyleSheet("font-size:12px;font-weight:700;color:#ded4f2;")
        version = QLabel(f"BETA  •  {APP_VERSION}")
        version.setObjectName("muted")
        version.setStyleSheet("font-size:10px;color:#70677f;")
        top.addWidget(mini_logo)
        top.addWidget(brand)
        top.addWidget(version)
        top.addStretch(1)
        top.addWidget(QLabel("MULTI-GAME OPTIMIZER"))
        top.itemAt(top.count() - 1).widget().setStyleSheet("font-size:9px;font-weight:700;letter-spacing:1px;color:#71667f;")
        for glyph, slot in (("—", self.showMinimized), ("□", self._toggle_maximize), ("×", self.close)):
            button = QPushButton(glyph)
            button.setFixedSize(30, 25)
            button.setStyleSheet("QPushButton{background:transparent;border:0;color:#978ba9;font-size:16px;padding:0;border-radius:6px;} QPushButton:hover{background:#262032;color:white;}")
            button.clicked.connect(slot)
            top.addWidget(button)
        outer_layout.addWidget(topbar)
        topbar.mousePressEvent = self._start_drag  # type: ignore[method-assign]
        topbar.mouseMoveEvent = self._do_drag  # type: ignore[method-assign]

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer_layout.addLayout(body, 1)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(228)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(15, 19, 15, 15)
        side.setSpacing(7)
        logo_row = QHBoxLayout()
        logo = QLabel("F")
        logo.setFixedSize(42, 42)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet("background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #a78bfa, stop:1 #6d28d9);border-radius:13px;font-size:25px;font-weight:900;color:white;")
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        logo_title = QLabel("ForGo")
        logo_title.setStyleSheet("font-size:20px;font-weight:800;letter-spacing:-.5px;color:#ffffff;")
        logo_subtitle = QLabel("MULTI-GAME OPTIMIZER")
        logo_subtitle.setStyleSheet("font-size:9px;font-weight:700;letter-spacing:1.1px;color:#8e7ca8;")
        title_col.addWidget(logo_title)
        title_col.addWidget(logo_subtitle)
        logo_row.addWidget(logo)
        logo_row.addSpacing(10)
        logo_row.addLayout(title_col)
        logo_row.addStretch(1)
        side.addLayout(logo_row)
        side.addSpacing(23)
        navcaption = QLabel("РАБОЧЕЕ ПРОСТРАНСТВО")
        navcaption.setObjectName("smallCaps")
        navcaption.setContentsMargins(7, 0, 0, 4)
        side.addWidget(navcaption)
        for glyph, text, page in [
            ("▦", "Игры", "home"),
            ("✦", "Оптимизация", "boost"),
            ("⚙", "Твики Windows", "tweaks"),
            ("⌘", "Инструменты", "tools"),
            ("☷", "Настройки", "settings"),
        ]:
            btn = NavButton(glyph, text, page)
            btn.clicked.connect(lambda checked=False, p=page: self._apply_nav(p))
            self.nav_buttons[page] = btn
            side.addWidget(btn)
        side.addStretch(1)
        mini_status = Card(alt=True)
        status_layout = QVBoxLayout(mini_status)
        status_layout.setContentsMargins(13, 13, 13, 13)
        status_layout.setSpacing(7)
        status_line = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet("color:#7de5ad;font-size:10px;")
        live = QLabel("ИГРОВОЙ ОПТИМИЗАТОР")
        live.setStyleSheet("color:#d2c7e4;font-size:9px;font-weight:800;letter-spacing:.6px;")
        status_line.addWidget(dot)
        status_line.addWidget(live)
        status_line.addStretch(1)
        status_layout.addLayout(status_line)
        safety = QLabel("Запуск игр и безопасные системные настройки.")
        safety.setWordWrap(True)
        safety.setObjectName("muted")
        safety.setStyleSheet("font-size:11px;line-height:1.3;color:#8c829d;")
        status_layout.addWidget(safety)
        side.addWidget(mini_status)
        side.addSpacing(7)
        build_lbl = QLabel("FORGO  /  WINDOWS 10–11")
        build_lbl.setObjectName("smallCaps")
        build_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(build_lbl)
        # Add the sidebar widget to the horizontal body layout. `side` is its QVBoxLayout.
        body.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.pages: dict[str, QWidget] = {}
        self.pages["home"] = self._page_home()
        self.pages["boost"] = self._page_boost()
        self.pages["tools"] = self._page_tools()
        self.pages["tweaks"] = self._page_tweaks()
        self.pages["settings"] = self._page_settings()
        for page in self.pages.values():
            self.stack.addWidget(page)
        body.addWidget(self.stack, 1)
        self.page_transition: QPropertyAnimation | None = None
        self._update_game_selection_ui()

    def _scroll_page(self) -> tuple[QScrollArea, QWidget, QVBoxLayout]:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setStyleSheet("background:transparent;")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(28, 27, 28, 28)
        layout.setSpacing(18)
        scroll.setWidget(holder)
        return scroll, holder, layout

    def _header(self, title: str, subtitle: str, eyebrow: str = "FORGO / CONTROL CENTER") -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        lab = QLabel(eyebrow)
        lab.setObjectName("eyebrow")
        tit = QLabel(title)
        tit.setObjectName("pageTitle")
        sub = QLabel(subtitle)
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        layout.addWidget(lab)
        layout.addWidget(tit)
        layout.addWidget(sub)
        return wrap

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    @staticmethod
    def _muted(text: str, size: int | None = None) -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        label.setWordWrap(True)
        if size:
            label.setStyleSheet(f"color:#9c94b2;font-size:{size}px;")
        return label

    @staticmethod
    def _primary(text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("primary")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setMinimumHeight(42)
        return btn

    @staticmethod
    def _shadow(widget: QWidget, blur: int = 20, y: int = 4, alpha: int = 35) -> None:
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(blur)
        effect.setOffset(0, y)
        effect.setColor(QColor(0, 0, 0, alpha))
        widget.setGraphicsEffect(effect)

    def _page_home(self) -> QWidget:
        """Main screen: game hub. Games are the primary purpose of ForGo."""
        scroll, _, root = self._scroll_page()
        root.addWidget(self._header("Игры", "Выбери игру, запусти её через официальный клиент и настрой Windows под игровой процесс.", "FORGO / GAME HUB"))

        selected = Card(alt=True)
        selected.setMinimumHeight(128)
        selected_row = QHBoxLayout(selected)
        selected_row.setContentsMargins(18, 16, 18, 16)
        selected_row.setSpacing(15)
        self.home_selected_logo = QLabel()
        self.home_selected_logo.setFixedSize(76, 76)
        self.home_selected_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.home_selected_logo.setStyleSheet("background:transparent;border:0;")
        selected_row.addWidget(self.home_selected_logo)

        selected_text = QVBoxLayout()
        selected_text.setSpacing(5)
        selected_caption = QLabel("ВЫБРАННАЯ ИГРА")
        selected_caption.setObjectName("eyebrow")
        self.games_selected_label = QLabel("Fortnite")
        self.games_selected_label.setObjectName("pageTitle")
        self.games_selected_label.setStyleSheet("font-size:22px;font-weight:800;")
        self.games_selected_hint = self._muted("Epic Games Launcher")
        selected_text.addWidget(selected_caption)
        selected_text.addWidget(self.games_selected_label)
        selected_text.addWidget(self.games_selected_hint)
        selected_row.addLayout(selected_text, 1)

        actions = QVBoxLayout()
        actions.setSpacing(8)
        self.games_launch_selected = self._primary("▶  Запустить игру")
        self.games_launch_selected.clicked.connect(self._launch_game)
        self.games_launch_selected.setMinimumWidth(178)
        self.games_optimize_selected = QPushButton("✦  Оптимизировать")
        self.games_optimize_selected.setMinimumHeight(40)
        self.games_optimize_selected.clicked.connect(lambda: self.apply_recommended(from_home=True))
        actions.addWidget(self.games_launch_selected)
        actions.addWidget(self.games_optimize_selected)
        selected_row.addLayout(actions)
        root.addWidget(selected)

        library_bar = QHBoxLayout()
        library_title = self._section_title("Библиотека")
        library_bar.addWidget(library_title)
        library_bar.addStretch(1)
        self.game_results_label = QLabel(f"{len(GAME_CATALOG)} игр")
        self.game_results_label.setObjectName("muted")
        library_bar.addWidget(self.game_results_label)
        root.addLayout(library_bar)

        self.game_search = QLineEdit()
        self.game_search.setPlaceholderText("⌕  Найти игру по названию или лаунчеру…")
        self.game_search.setClearButtonEnabled(True)
        root.addWidget(self.game_search)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        self.game_grid = grid
        for index, (game_id, game) in enumerate(GAME_CATALOG.items()):
            card = Card()
            card.setMinimumHeight(174)
            card.setProperty("selectedGame", False)
            self.game_card_widgets[game_id] = card
            layout = QVBoxLayout(card)
            layout.setContentsMargins(15, 14, 15, 14)
            layout.setSpacing(10)

            top = QHBoxLayout()
            top.setSpacing(12)
            mark = QLabel()
            mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mark.setFixedSize(66, 66)
            mark.setStyleSheet("background:transparent;border:0;")
            image_path = Path(__file__).parent / "assets" / "games" / f"{game_id}.png"
            pixmap = QPixmap(str(image_path)) if image_path.exists() else QPixmap()
            if not pixmap.isNull():
                mark.setPixmap(pixmap.scaled(66, 66, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                mark.setText(game.get("tag", game["name"][:3]))
                mark.setStyleSheet("background:rgba(255,255,255,18);border:1px solid rgba(255,255,255,50);border-radius:15px;font-size:16px;font-weight:900;")
            title_stack = QVBoxLayout()
            title_stack.setSpacing(4)
            title = QLabel(game["name"])
            title.setStyleSheet("font-size:15px;font-weight:800;")
            client = QLabel(game["client"])
            client.setObjectName("muted")
            client.setStyleSheet("font-size:10px;")
            client.setWordWrap(True)
            title_stack.addWidget(title)
            title_stack.addWidget(client)
            title_stack.addStretch(1)
            top.addWidget(mark)
            top.addLayout(title_stack, 1)
            layout.addLayout(top)

            description = QLabel(game["description"])
            description.setObjectName("muted")
            description.setWordWrap(True)
            description.setMinimumHeight(30)
            layout.addWidget(description)

            actions_row = QHBoxLayout()
            actions_row.setSpacing(8)
            choose = QPushButton("Выбрать")
            choose.setObjectName("tiny")
            choose.setMinimumHeight(36)
            choose.clicked.connect(lambda checked=False, key=game_id: self._select_game(key))
            launch = self._primary("▶  Запустить")
            launch.setMinimumHeight(36)
            launch.clicked.connect(lambda checked=False, key=game_id: self._launch_game_for(key))
            self.game_card_select_buttons[game_id] = choose
            self.game_card_launch_buttons[game_id] = launch
            actions_row.addWidget(choose, 1)
            actions_row.addWidget(launch, 1)
            layout.addLayout(actions_row)
            grid.addWidget(card, index // 2, index % 2)
        root.addLayout(grid)
        self.game_search.textChanged.connect(self._filter_games)

        footer = self._muted("Профили Windows обратимы. ForGo не меняет игровые файлы и не отключает античит.", 11)
        root.addWidget(footer)
        root.addStretch(1)
        return scroll

    def _filter_games(self, query: str) -> None:
        if self.game_grid is None:
            return
        query = query.strip().casefold()
        for game_id, card in self.game_card_widgets.items():
            game = GAME_CATALOG[game_id]
            searchable = " ".join((game["name"], game["client"], game["description"], game_id)).casefold()
            card.setVisible(not query or query in searchable)
        # Repack visible cards so search results don't leave empty holes.
        while self.game_grid.count():
            self.game_grid.takeAt(0)
        visible = [card for card in self.game_card_widgets.values() if not card.isHidden()]
        for index, card in enumerate(visible):
            self.game_grid.addWidget(card, index // 2, index % 2)
        if hasattr(self, "game_results_label"):
            self.game_results_label.setText(f"{len(visible)} из {len(GAME_CATALOG)} игр" if query else f"{len(GAME_CATALOG)} игр")

    def _select_game(self, game_id: str, show_toast: bool = True) -> None:
        if game_id not in GAME_CATALOG:
            return
        previous = self.store.data.get("selected_game", "fortnite")
        self.store.data["selected_game"] = game_id
        self.store.save()
        if previous != game_id and self.priority_active:
            # Restore any previous game's process priority before following the new selection.
            self._restore_priorities()
        self._update_game_selection_ui()
        if show_toast:
            self._toast(f"Выбрана игра: {GAME_CATALOG[game_id]['name']}")

    def _update_game_selection_ui(self) -> None:
        game_id = str(self.store.data.get("selected_game", "fortnite"))
        game = GAME_CATALOG.get(game_id, GAME_CATALOG["fortnite"])
        if hasattr(self, "games_selected_label"):
            self.games_selected_label.setText(game["name"])
        if hasattr(self, "games_selected_hint"):
            self.games_selected_hint.setText(f"{game['client']}  ·  системный профиль обратим")
        if hasattr(self, "games_launch_selected"):
            self.games_launch_selected.setText(f"▶  Запустить {game['name']}")
        if hasattr(self, "home_selected_logo"):
            image_path = Path(__file__).parent / "assets" / "games" / f"{game_id}.png"
            pixmap = QPixmap(str(image_path)) if image_path.exists() else QPixmap()
            if not pixmap.isNull():
                self.home_selected_logo.setPixmap(pixmap.scaled(76, 76, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        for key, button in self.game_card_select_buttons.items():
            button.setText("✓  Выбрана" if key == game_id else "Выбрать")
            card = self.game_card_widgets.get(key)
            if card is not None:
                card.setProperty("selectedGame", key == game_id)
                card.style().unpolish(card)
                card.style().polish(card)
                card.update()
        if hasattr(self, "priority_title_label"):
            self.priority_title_label.setText(f"Приоритет: {game['name']}")
        if hasattr(self, "priority_desc_label"):
            self.priority_desc_label.setText(f"Временно задаёт процессу {game['name']} приоритет Above normal, пока ForGo открыт. Не использует Realtime.")

    def _launch_game_for(self, game_id: str) -> None:
        self._select_game(game_id, show_toast=False)
        self._launch_game()

    def _page_boost(self) -> QWidget:
        scroll, _, root = self._scroll_page()
        root.addWidget(self._header("Оптимизация", "Рекомендуемые системные настройки и временный приоритет выбранной игры.", "FORGO / GAME OPTIMIZATION"))
        summary = Card(alt=True)
        sl = QHBoxLayout(summary); sl.setContentsMargins(20, 17, 20, 17); sl.setSpacing(15)
        icon = QLabel("✦"); icon.setAlignment(Qt.AlignmentFlag.AlignCenter); icon.setFixedSize(44, 44)
        icon.setStyleSheet("background:#2b1d42;border:1px solid #56397d;border-radius:13px;color:#cdb8ff;font-size:24px;")
        col = QVBoxLayout(); col.setSpacing(4)
        col.addWidget(self._section_title("Профиль Competitive"))
        col.addWidget(self._muted("Общие настройки Windows подходят для выбранной игры. Файлы игр и античит не меняются."))
        sl.addWidget(icon); sl.addLayout(col, 1)
        self.apply_boost_btn = self._primary("Применить рекомендуемые")
        self.apply_boost_btn.clicked.connect(lambda: self.apply_recommended(from_home=False))
        sl.addWidget(self.apply_boost_btn)
        root.addWidget(summary)

        root.addWidget(self._section_title("Состояние системы"))
        grid = QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(12)
        for i, (title, desc) in enumerate([
            ("Процессор", "Текущая загрузка CPU"),
            ("Память", "Текущая занятость RAM"),
        ]):
            card = Card(); lay = QVBoxLayout(card); lay.setContentsMargins(17, 15, 17, 15); lay.setSpacing(8)
            t = QLabel(title); t.setObjectName("muted")
            val = QLabel("—"); val.setObjectName("bigNumber")
            hint = QLabel(desc); hint.setObjectName("muted")
            if i == 0: self.boost_cpu = val
            else: self.boost_ram = val
            lay.addWidget(t); lay.addWidget(val); lay.addWidget(hint)
            grid.addWidget(card, 0, i)
        root.addLayout(grid)

        root.addWidget(self._section_title("Игровой процесс"))
        process = Card(); pl = QHBoxLayout(process); pl.setContentsMargins(17, 15, 17, 15)
        pcol = QVBoxLayout(); pcol.setSpacing(5); self.priority_title_label = QLabel("Приоритет выбранной игры"); self.priority_desc_label = self._muted("Временно задаёт выбранной игре приоритет Above normal, пока ForGo открыт. Не использует рискованный Realtime."); pcol.addWidget(self.priority_title_label); pcol.addWidget(self.priority_desc_label)
        pl.addLayout(pcol, 1)
        self.priority_button = QPushButton("Включить приоритет")
        self.priority_button.clicked.connect(self._toggle_priority)
        pl.addWidget(self.priority_button)
        root.addWidget(process)
        note = QLabel("Важно: реальный FPS зависит от видеокарты, процессора, температуры, драйверов и настроек графики. ForGo не внедряется в игру и не подменяет её счётчики.")
        note.setObjectName("muted"); note.setWordWrap(True); note.setStyleSheet("color:#8f849f;padding:4px 2px;")
        root.addWidget(note)
        root.addStretch(1)
        return scroll

    def _page_tools(self) -> QWidget:
        scroll, _, root = self._scroll_page()
        root.addWidget(self._header("Инструменты", "Диагностика компьютера и обслуживание Windows без сомнительных твиков.", "FORGO / TOOLS"))

        root.addWidget(self._section_title("Состояние ПК"))
        system = QGridLayout()
        system.setHorizontalSpacing(12)
        system.setVerticalSpacing(12)
        try:
            ram_total = psutil.virtual_memory().total / (1024 ** 3)
            ram_text = f"{ram_total:.0f} GB"
        except Exception:
            ram_text = "—"
        try:
            disk_root = Path.home().anchor or str(Path.home())
            free_gb = psutil.disk_usage(disk_root).free / (1024 ** 3)
            disk_text = f"{free_gb:.0f} GB свободно"
        except Exception:
            disk_text = "—"
        system_specs = [
            ("Windows", f"{platform.system()} {platform.release()}", "Версия операционной системы"),
            ("Процессор", f"{psutil.cpu_count(logical=True) or '—'} потоков", platform.processor() or "Модель доступна не всегда"),
            ("Оперативная память", ram_text, "Общий объём RAM"),
            ("Системный диск", disk_text, "Свободное место"),
        ]
        for index, (title, value, hint) in enumerate(system_specs):
            card = Card()
            card.setMinimumHeight(100)
            lay = QVBoxLayout(card)
            lay.setContentsMargins(15, 13, 15, 13)
            lay.setSpacing(5)
            label = QLabel(title)
            label.setObjectName("muted")
            amount = QLabel(value)
            amount.setObjectName("bigNumber")
            amount.setStyleSheet("font-size:22px;font-weight:800;")
            desc = QLabel(hint)
            desc.setObjectName("muted")
            desc.setWordWrap(True)
            desc.setStyleSheet("font-size:10px;")
            lay.addWidget(label)
            lay.addWidget(amount)
            lay.addWidget(desc)
            system.addWidget(card, index // 2, index % 2)
        root.addLayout(system)

        load_card = Card(alt=True)
        load_layout = QVBoxLayout(load_card)
        load_layout.setContentsMargins(16, 14, 16, 14)
        load_layout.setSpacing(9)
        load_layout.addWidget(self._section_title("Нагрузка сейчас"))
        load_row = QHBoxLayout()
        cpu_col = QVBoxLayout()
        cpu_head = QLabel("CPU")
        cpu_head.setObjectName("muted")
        self.tools_cpu_value = QLabel("—")
        self.tools_cpu_value.setObjectName("bigNumber")
        self.tools_cpu_value.setStyleSheet("font-size:22px;font-weight:800;")
        cpu_col.addWidget(cpu_head); cpu_col.addWidget(self.tools_cpu_value)
        self.tools_cpu_bar = QProgressBar(); self.tools_cpu_bar.setRange(0, 100); self.tools_cpu_bar.setTextVisible(False)
        cpu_col.addWidget(self.tools_cpu_bar)
        ram_col = QVBoxLayout()
        ram_head = QLabel("RAM")
        ram_head.setObjectName("muted")
        self.tools_ram_value = QLabel("—")
        self.tools_ram_value.setObjectName("bigNumber")
        self.tools_ram_value.setStyleSheet("font-size:22px;font-weight:800;")
        ram_col.addWidget(ram_head); ram_col.addWidget(self.tools_ram_value)
        self.tools_ram_bar = QProgressBar(); self.tools_ram_bar.setRange(0, 100); self.tools_ram_bar.setTextVisible(False)
        ram_col.addWidget(self.tools_ram_bar)
        load_row.addLayout(cpu_col, 1); load_row.addLayout(ram_col, 1)
        load_layout.addLayout(load_row)
        root.addWidget(load_card)

        proc_header = QHBoxLayout()
        proc_header.addWidget(self._section_title("Самые ресурсоёмкие процессы"))
        proc_header.addStretch(1)
        refresh_proc = QPushButton("↻  Обновить")
        refresh_proc.setObjectName("tiny")
        refresh_proc.clicked.connect(self._refresh_process_list)
        proc_header.addWidget(refresh_proc)
        root.addLayout(proc_header)
        proc_card = Card()
        proc_layout = QVBoxLayout(proc_card)
        proc_layout.setContentsMargins(15, 12, 15, 12)
        proc_layout.setSpacing(6)
        self.process_list_layout = proc_layout
        root.addWidget(proc_card)
        self._refresh_process_list()

        root.addWidget(self._section_title("Обслуживание"))
        maintenance = QGridLayout()
        maintenance.setHorizontalSpacing(12)
        maintenance.setVerticalSpacing(12)
        dns_card = Card()
        dns_layout = QVBoxLayout(dns_card); dns_layout.setContentsMargins(15, 14, 15, 14); dns_layout.setSpacing(8)
        dns_layout.addWidget(self._section_title("DNS-кэш"))
        dns_layout.addWidget(self._muted("Полезно при проблемах с разрешением доменных имён; не обещает снизить ping в матче."))
        dns_button = QPushButton("Очистить DNS-кэш")
        dns_button.clicked.connect(self._flush_dns)
        dns_layout.addWidget(dns_button)
        temp_card = Card()
        temp_layout = QVBoxLayout(temp_card); temp_layout.setContentsMargins(15, 14, 15, 14); temp_layout.setSpacing(8)
        temp_layout.addWidget(self._section_title("Временные файлы"))
        temp_layout.addWidget(self._muted("Удаляет доступные временные файлы старше 24 часов, чтобы освободить место."))
        temp_button = QPushButton("Очистить временные файлы")
        temp_button.clicked.connect(self._clean_temp)
        temp_layout.addWidget(temp_button)
        startup_card = Card()
        startup_layout = QVBoxLayout(startup_card); startup_layout.setContentsMargins(15, 14, 15, 14); startup_layout.setSpacing(8)
        startup_layout.addWidget(self._section_title("Автозагрузка"))
        startup_layout.addWidget(self._muted("Открой системные настройки и отключи ненужные приложения вручную."))
        startup_button = QPushButton("Управление автозагрузкой")
        startup_button.clicked.connect(self._open_startup_settings)
        startup_layout.addWidget(startup_button)
        maintenance.addWidget(dns_card, 0, 0)
        maintenance.addWidget(temp_card, 0, 1)
        maintenance.addWidget(startup_card, 1, 0, 1, 2)
        root.addLayout(maintenance)
        root.addStretch(1)
        return scroll

    def _refresh_process_list(self) -> None:
        if not hasattr(self, "process_list_layout"):
            return
        while self.process_list_layout.count():
            item = self.process_list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        entries: list[tuple[str, float]] = []
        try:
            for proc in psutil.process_iter(["name", "memory_info"]):
                try:
                    info = proc.info
                    mem = info.get("memory_info")
                    name = str(info.get("name") or "Неизвестный процесс")
                    if mem:
                        entries.append((name, float(mem.rss) / (1024 ** 2)))
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                    continue
        except Exception:
            entries = []
        entries.sort(key=lambda item: item[1], reverse=True)
        for name, memory_mb in entries[:8]:
            row_widget = QWidget()
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(0, 2, 0, 2)
            process_label = QLabel(name)
            process_label.setMinimumWidth(0)
            process_label.setObjectName("muted")
            memory_label = QLabel(f"{memory_mb:,.0f} MB".replace(",", " "))
            memory_label.setStyleSheet("font-weight:750;")
            row.addWidget(process_label, 1)
            row.addWidget(memory_label)
            self.process_list_layout.addWidget(row_widget)
        if not entries:
            self.process_list_layout.addWidget(self._muted("Не удалось получить список процессов в этой среде."))
        self.process_list_layout.addWidget(self._muted("Показана память процессов; сама по себе высокая цифра не означает проблему.", 10))

    def _open_startup_settings(self) -> None:
        if not IS_WINDOWS:
            QMessageBox.information(self, "Автозагрузка", "Настройки автозагрузки открываются только в Windows 10/11.")
            return
        try:
            os.startfile("ms-settings:startupapps")  # type: ignore[attr-defined]
        except Exception:
            try:
                subprocess.Popen(["explorer.exe", "ms-settings:startupapps"])
            except Exception as exc:
                QMessageBox.warning(self, "Автозагрузка", f"Не удалось открыть настройки Windows.\n{exc}")

    def _tweak_row(self, title: str, subtitle: str, tweak_id: str, recommended: bool = True, warning: bool = False) -> QWidget:
        row = Card()
        h = QHBoxLayout(row); h.setContentsMargins(16, 13, 16, 13); h.setSpacing(12)
        col = QVBoxLayout(); col.setSpacing(4)
        t = QLabel(title); t.setStyleSheet("font-size:13px;font-weight:700;color:#eee9f8;")
        sub = QLabel(subtitle); sub.setObjectName("muted"); sub.setWordWrap(True); sub.setStyleSheet("color:#9389a6;font-size:11px;")
        col.addWidget(t); col.addWidget(sub)
        if recommended or warning:
            badge = QLabel("РЕКОМЕНДУЕМЫЙ" if recommended and not warning else "ОПЦИОНАЛЬНО")
            badge.setStyleSheet("background:#251b37;color:#bba0fb;border-radius:5px;padding:4px 6px;font-size:8px;font-weight:800;letter-spacing:.4px;")
            col.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
        h.addLayout(col, 1)
        check = GlassSwitch(self.store.active(tweak_id))
        check.setCursor(Qt.CursorShape.PointingHandCursor)
        check.tweak_id = tweak_id  # type: ignore[attr-defined]
        check.toggled.connect(lambda enabled, c=check: self._change_tweak(c.tweak_id, enabled, c))
        self.tweak_checks[tweak_id] = check
        h.addWidget(check, 0, Qt.AlignmentFlag.AlignVCenter)
        return row

    def _page_tweaks(self) -> QWidget:
        scroll, _, root = self._scroll_page()
        root.addWidget(self._header("Библиотека твиков", "100+ переключателей с пояснениями. Ищи по названию, фильтруй по категории и возвращай исходные значения.", "FORGO / SYSTEM TUNING"))
        banner = Card(alt=True)
        bl = QHBoxLayout(banner); bl.setContentsMargins(18, 16, 18, 16); bl.setSpacing(13)
        icon = QLabel("✦"); icon.setAlignment(Qt.AlignmentFlag.AlignCenter); icon.setFixedSize(44, 44)
        icon.setStyleSheet("background:rgba(160,122,255,24);border:1px solid rgba(203,175,255,110);border-radius:14px;color:#d7c3ff;font-size:23px;")
        col = QVBoxLayout(); col.setSpacing(4)
        col.addWidget(self._section_title("Рекомендуемый профиль"))
        col.addWidget(self._muted("Включает только проверенные базовые параметры. Остальные настройки необязательны и могут влиять на привычный вид Windows."))
        bl.addWidget(icon); bl.addLayout(col, 1)
        btn = self._primary("Применить рекомендуемые")
        btn.clicked.connect(lambda: self.apply_recommended(from_home=False))
        bl.addWidget(btn)
        root.addWidget(banner)

        tools = Card(); tl = QHBoxLayout(tools); tl.setContentsMargins(13, 12, 13, 12); tl.setSpacing(9)
        self.tweak_search = QLineEdit(); self.tweak_search.setPlaceholderText("⌕  Найти твик по названию или описанию…")
        self.tweak_search.setClearButtonEnabled(True)
        self.tweak_category = QComboBox(); self.tweak_category.setMinimumWidth(205)
        self.tweak_category.addItem("Все категории", "Все категории")
        for cat in sorted({item["category"] for item in TWEAK_CATALOG}): self.tweak_category.addItem(cat, cat)
        tl.addWidget(self.tweak_search, 1); tl.addWidget(self.tweak_category)
        root.addWidget(tools)

        summary = QHBoxLayout()
        self.tweak_results_label = QLabel(f"{len(TWEAK_CATALOG)} настроек")
        self.tweak_results_label.setObjectName("eyebrow")
        summary.addWidget(self.tweak_results_label); summary.addStretch(1)
        restore = QPushButton("↺  Восстановить изменённое")
        restore.setObjectName("tiny"); restore.clicked.connect(self.restore_all)
        summary.addWidget(restore)
        root.addLayout(summary)

        self.tweak_checks: dict[str, GlassSwitch] = {}
        self.tweaks_container = QWidget(); self.tweaks_container.setStyleSheet("background:transparent;")
        self.tweaks_container_layout = QVBoxLayout(self.tweaks_container)
        self.tweaks_container_layout.setContentsMargins(0, 0, 0, 0)
        self.tweaks_container_layout.setSpacing(9)
        root.addWidget(self.tweaks_container)
        self.tweak_search.textChanged.connect(self._render_tweak_catalog)
        self.tweak_category.currentIndexChanged.connect(self._render_tweak_catalog)
        self._render_tweak_catalog()
        note = QLabel("Примечание: настройки удобства, приватности и оболочки не обещают прирост FPS. Часть параметров может игнорироваться отдельными сборками Windows. Все изменения реестра ограничены профилем текущего пользователя и имеют сохранённые исходные значения.")
        note.setObjectName("muted"); note.setWordWrap(True); note.setStyleSheet("font-size:11px;color:#a398b7;padding:5px 3px;")
        root.addWidget(note)
        root.addStretch(1)
        return scroll

    def _render_tweak_catalog(self, *_args) -> None:
        if not hasattr(self, "tweaks_container_layout"):
            return
        while self.tweaks_container_layout.count():
            item = self.tweaks_container_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.tweak_checks = {}
        query = self.tweak_search.text().strip().casefold() if hasattr(self, "tweak_search") else ""
        category = self.tweak_category.currentData() if hasattr(self, "tweak_category") else "Все категории"
        filtered = [item for item in TWEAK_CATALOG
                    if (category == "Все категории" or item["category"] == category)
                    and (not query or query in (item["title"] + " " + item["description"] + " " + item["category"]).casefold())]
        for cat in dict.fromkeys(item["category"] for item in filtered):
            self.tweaks_container_layout.addWidget(self._section_title(cat))
            for item in (x for x in filtered if x["category"] == cat):
                self.tweaks_container_layout.addWidget(self._tweak_row(
                    item["title"], item["description"], item["id"],
                    recommended=item["recommended"], warning=item["warning"]))
        if not filtered:
            empty = Card(); e = QVBoxLayout(empty); e.setContentsMargins(20, 20, 20, 20)
            e.addWidget(self._section_title("Ничего не найдено"))
            e.addWidget(self._muted("Попробуй изменить запрос или выбрать другую категорию."))
            self.tweaks_container_layout.addWidget(empty)
        self.tweaks_container_layout.addStretch(1)
        if hasattr(self, "tweak_results_label"):
            self.tweak_results_label.setText(f"{len(filtered)} из {len(TWEAK_CATALOG)} настроек")
        self._sync_tweak_ui()

    def _page_settings(self) -> QWidget:
        scroll, _, root = self._scroll_page()
        root.addWidget(self._header("Настройки", "Внешний вид и поведение ForGo.", "FORGO / PREFERENCES"))
        appearance = Card(alt=True)
        al = QVBoxLayout(appearance); al.setContentsMargins(20, 18, 20, 18); al.setSpacing(11)
        al.addWidget(self._section_title("Внешний вид"))
        al.addWidget(self._muted("Liquid Glass с прозрачными панелями или строгая чёрная тема. Выбор сохраняется на этом ПК."))
        self.theme_combo = QComboBox()
        for theme_id, meta in THEMES.items():
            self.theme_combo.addItem(meta["label"], theme_id)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self.store.data.get("theme", "liquid_glass"))))
        self.theme_combo.currentIndexChanged.connect(lambda _i: self._set_theme(self.theme_combo.currentData()))
        al.addWidget(self.theme_combo)
        swatches = QHBoxLayout(); swatches.setSpacing(8)
        for label, color in (("LIQUID GLASS", "#a78bfa"), ("BLACK", "#a8a0ba")):
            pill = QLabel("  ●  " + label + "  ")
            pill.setStyleSheet(f"background:rgba(255,255,255,6);border:1px solid {color};border-radius:9px;color:{color};font-size:9px;font-weight:800;padding:7px;")
            swatches.addWidget(pill)
        al.addLayout(swatches)
        root.addWidget(appearance)

        about = Card(); bl = QVBoxLayout(about); bl.setContentsMargins(20, 18, 20, 18); bl.setSpacing(8)
        bl.addWidget(self._section_title("ForGo Game Hub"))
        version = QLabel(f"Версия {APP_VERSION} · Windows 10 / 11 · x64")
        version.setStyleSheet("color:#c1b2db;font-weight:700;")
        bl.addWidget(version)
        bl.addWidget(self._muted(f"В каталоге {len(GAME_CATALOG)} игр и {len(TWEAK_CATALOG)} системных настроек. ForGo не меняет файлы игр и не отключает античит."))
        root.addWidget(about)

        privacy = Card(alt=True); pv = QVBoxLayout(privacy); pv.setContentsMargins(18, 15, 18, 15); pv.setSpacing(6)
        pv.addWidget(self._section_title("Данные и безопасность"))
        pv.addWidget(self._muted(f"Настройки и резервные значения хранятся локально: {app_data_dir()}"))
        pv.addWidget(self._muted("ForGo не отключает Microsoft Defender, брандмауэр, античит или Windows Update. Твики, которые меняют реестр пользователя, можно откатить."))
        root.addWidget(privacy)
        root.addStretch(1)
        return scroll

    def _set_theme(self, theme_id: str | None) -> None:
        if theme_id not in THEMES:
            theme_id = "liquid_glass"
        self.store.data["theme"] = theme_id
        self.store.save()
        self._apply_theme(theme_id)
        self._toast(f"Тема: {THEMES[theme_id]['label']}")

    def _apply_theme(self, theme_id: str) -> None:
        # Migrate saved selections from the retired purple-glass theme.
        if theme_id not in THEMES:
            theme_id = "liquid_glass"
            self.store.data["theme"] = theme_id
            self.store.save()
        palettes = {
            "liquid_glass": {"dark": True, "accent": "#b7a4ff", "accent2": "#7658d6"},
            "black": {"dark": True, "accent": "#aaa3bb", "accent2": "#686171"},
        }
        palette = palettes[theme_id]
        accent, accent2 = palette["accent"], palette["accent2"]
        self.liquid_root.set_theme(theme_id)
        self.store.data["theme"] = theme_id
        self.store.data["ui_version"] = 5
        self.store.save()

        # Only map accents globally. Light-theme surface and text mappings are isolated
        # below so dark themes never accidentally inherit light-theme text colors.
        replacements: dict[str, str] = {
            "#8b5cf6": accent2, "#9b70ff": accent, "#a78bfa": accent,
            "#6d28d9": accent2, "#b7a0ff": accent, "#8152df": accent2,
            "#a58ae5": accent, "#594078": accent2, "#56397d": accent2,
            "#cdb8ff": accent, "#c6a8ff": accent, "#d8c6ff": accent,
            "#c1b2db": accent, "#ccb6ff": accent, "#bba0fb": accent,
            "#7754ae": accent2, "#53327e": accent2, "#63458c": accent2,
        }
        app = QApplication.instance()
        theme_overrides = {
            "liquid_glass": r"""
QMainWindow { background: transparent; }
QWidget#root { background: transparent; color: #f7f7fc; }
QWidget { color: #f5f3fb; }
QFrame#sidebar { background: rgba(22,20,36,164); border-right: 1px solid rgba(255,255,255,42); border-top: 1px solid rgba(255,255,255,36); }
QFrame#topbar { background: rgba(25,22,39,150); border-bottom: 1px solid rgba(255,255,255,48); }
QFrame#card { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(255,255,255,49),stop:0.12 rgba(157,145,190,39),stop:0.42 rgba(47,41,67,158),stop:1 rgba(25,23,39,170)); border: 1px solid rgba(255,255,255,62); border-top-color: rgba(255,255,255,155); }
QFrame#cardAlt { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(255,255,255,67),stop:0.18 rgba(173,157,220,58),stop:1 rgba(34,29,52,167)); border-color: rgba(235,227,255,112); border-top-color: rgba(255,255,255,185); }
QFrame#hero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(196,181,255,91),stop:0.18 rgba(113,99,161,82),stop:0.52 rgba(39,33,60,161),stop:1 rgba(21,20,34,177)); border-color: rgba(239,230,255,166); border-top-color: rgba(255,255,255,220); }
QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 rgba(255,255,255,60),stop:0.12 rgba(255,255,255,28),stop:0.55 rgba(255,255,255,13),stop:1 rgba(255,255,255,8)); border: 1px solid rgba(255,255,255,86); border-top: 1px solid rgba(255,255,255,190); color: #fbf9ff; }
QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 rgba(255,255,255,88),stop:1 rgba(183,164,255,50)); border-color: rgba(255,255,255,230); }
QPushButton:pressed { background: rgba(142,119,221,150); border-color: white; }
QPushButton#primary { color: #ffffff; background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(228,219,255,245),stop:0.22 rgba(179,159,255,245),stop:0.62 rgba(130,103,218,247),stop:1 rgba(83,65,151,248)); border-color: rgba(255,255,255,235); border-top-color: white; }
QPushButton#primary:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #f3eeff,stop:0.45 #c2b2ff,stop:1 #9c80f3); }
QPushButton#nav { color: #e4def2; background: rgba(255,255,255,8); border: 1px solid rgba(255,255,255,18); }
QPushButton#nav:hover { color: white; background: rgba(255,255,255,32); border-color: rgba(255,255,255,65); }
QPushButton#nav[active="true"] { color: #ffffff; background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 rgba(206,193,255,100),stop:0.48 rgba(143,124,220,65),stop:1 rgba(255,255,255,17)); border: 1px solid rgba(246,241,255,155); border-top-color: rgba(255,255,255,225); }
QLineEdit, QComboBox { color: #fbf9ff; background: rgba(255,255,255,31); border-color: rgba(255,255,255,84); border-top-color: rgba(255,255,255,180); }
QLineEdit:focus, QComboBox:focus { background: rgba(255,255,255,48); border-color: rgba(255,255,255,230); }
QComboBox QAbstractItemView { background: #211a31; color: #fbf9ff; selection-background-color: #6f51ac; }
QLabel#muted { color: #d1cbe0; }
QLabel#eyebrow, QLabel#smallCaps { color: #e4d9ff; }
QLabel#pageTitle, QLabel#sectionTitle, QLabel#bigNumber { color: #ffffff; }
QProgressBar { background: rgba(255,255,255,23); border-color: rgba(255,255,255,44); }
QProgressBar::chunk { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #eee8ff,stop:1 #9474f0); }
""",
            "black": r"""
QMainWindow { background: #09090c; }
QWidget { color: #f3f1f7; }
QFrame#sidebar { background: rgba(13,12,17,245); border-right: 1px solid rgba(255,255,255,20); }
QFrame#topbar { background: rgba(17,16,22,242); }
QFrame#card { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(255,255,255,18),stop:1 rgba(30,29,36,238)); border-color: rgba(255,255,255,35); border-top-color: rgba(255,255,255,85); }
QFrame#cardAlt { background: rgba(42,40,50,235); border-color: rgba(190,183,210,70); }
QFrame#hero { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 rgba(126,119,145,75),stop:1 rgba(18,17,23,242)); border-color: rgba(220,216,235,100); }
QPushButton#primary { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #a8a1b8,stop:1 #57525f); }
QPushButton#nav[active="true"] { background: rgba(165,158,183,42); border-color: rgba(215,209,230,92); }
QLabel#muted { color: #b7b1c5; }
""",
        }
        if app:
            stylesheet = self._map_colors(CSS, replacements, theme_id)
            stylesheet += theme_overrides.get(theme_id, "")
            app.setStyleSheet(stylesheet)
        for widget in self.findChildren(QWidget):
            original = widget.property("_forgo_original_stylesheet")
            if original is None:
                original = widget.styleSheet()
                widget.setProperty("_forgo_original_stylesheet", original)
            if original:
                if widget.objectName() == "overviewLogoMark":
                    # The brand tile must remain opaque white with black lettering in both themes.
                    widget.setStyleSheet(str(original))
                else:
                    widget.setStyleSheet(self._map_colors(str(original), replacements, theme_id))
            if isinstance(widget, GlassSwitch):
                widget.setAccent(accent)
        self.setProperty("forgoTheme", theme_id)
        # Set/remove the native Windows backdrop after the platform window handle exists.
        QTimer.singleShot(0, self._apply_native_glass)

    def _apply_native_glass(self) -> None:
        """Enable Windows acrylic blur behind the 35%-transparent Liquid Glass surfaces."""
        if not IS_WINDOWS:
            return
        try:
            hwnd = int(self.winId())
            class ACCENT_POLICY(ctypes.Structure):
                _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                            ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]
            class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
                _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.POINTER(ACCENT_POLICY)),
                            ("SizeOfData", ctypes.c_size_t)]
            accent_state = 4 if self.store.data.get("theme") == "liquid_glass" else 0
            # AABBGGRR: dark neutral tint. Alpha 0x9E is about 62% opaque/38% clear.
            alpha, red, green, blue = 0x9E, 17, 13, 29
            gradient = (alpha << 24) | (blue << 16) | (green << 8) | red
            policy = ACCENT_POLICY(accent_state, 2, gradient, 0)
            data = WINDOWCOMPOSITIONATTRIBDATA(19, ctypes.pointer(policy), ctypes.sizeof(policy))
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            setter = getattr(user32, "SetWindowCompositionAttribute", None)
            if setter:
                setter.argtypes = [ctypes.c_void_p, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
                setter.restype = ctypes.c_bool
                if setter(ctypes.c_void_p(hwnd), ctypes.byref(data)):
                    return
            # Windows 11 fallback: system transient backdrop, if available.
            dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
            value = ctypes.c_int(3 if accent_state else 1)  # transient / none
            dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 38, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            # CSS glass still works if the optional Windows native blur is unavailable.
            pass

    @staticmethod
    def _map_colors(text: str, replacements: dict[str, str], theme_id: str) -> str:
        def repl(match: re.Match[str]) -> str:
            color = match.group(0).lower()
            return replacements.get(color, color)
        result = re.sub(r"#[0-9a-fA-F]{6}", repl, text)
        # Keep inline RGBA accents in sync with the selected palette too; many tiny
        # labels and badges use RGBA rather than hex colors in their local stylesheets.
        if theme_id == "black":
            for old in ("rgba(139,92,246,", "rgba(197,171,255,", "rgba(190,166,255,",
                        "rgba(196,174,235,", "rgba(160,122,255,", "rgba(185,152,255,"):
                result = result.replace(old, "rgba(170,163,187,")
        return result

    def _apply_nav(self, page: str) -> None:
        if page not in self.pages:
            return
        for key, button in self.nav_buttons.items(): button.set_active(key == page)
        old_index = self.stack.currentIndex()
        target = self.pages[page]
        target_index = self.stack.indexOf(target)
        self.stack.setCurrentWidget(target)
        # Stable lightweight slide: no opacity effect is attached to the page tree, so
        # mouse hover/click repainting cannot blank the entire interface.
        if target_index != old_index and target.width() > 0:
            if self.page_transition is not None:
                self.page_transition.stop()
            direction = 1 if target_index >= old_index else -1
            target.move(direction * 9, 0)
            animation = QPropertyAnimation(target, b"pos", self)
            animation.setDuration(190)
            animation.setStartValue(QPoint(direction * 9, 0))
            animation.setEndValue(QPoint(0, 0))
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.page_transition = animation
            animation.start()
        self._sync_tweak_ui()
        if page == "tools":
            self._refresh_process_list()

    def _refresh_system(self) -> None:
        cpu = int(psutil.cpu_percent(interval=None))
        mem = psutil.virtual_memory()
        ram = int(mem.percent)
        for attr, value, bar_name in (("cpu_value", f"{cpu}%", "cpu_bar"), ("ram_value", f"{ram}%", "ram_bar")):
            label = getattr(self, attr, None)
            if label: label.setText(value)
            bar = getattr(self, bar_name, None)
            if bar: bar.setValue(cpu if bar_name == "cpu_bar" else ram)
        if hasattr(self, "boost_cpu"):
            self.boost_cpu.setText(f"{cpu}%")
            self.boost_ram.setText(f"{ram}%")
        if hasattr(self, "tools_cpu_value"):
            self.tools_cpu_value.setText(f"{cpu}%")
            self.tools_ram_value.setText(f"{ram}%")
            self.tools_cpu_bar.setValue(cpu)
            self.tools_ram_bar.setValue(ram)
        self._update_tweak_count()

    def _update_tweak_count(self) -> None:
        active = [x for x in self.store.data.get("active_tweaks", []) if x in REGISTRY_TWEAKS or x == "power_plan"]
        if hasattr(self, "tweak_count_label"):
            self.tweak_count_label.setText(f"{len(active)} активных настроек")

    def _sync_tweak_ui(self) -> None:
        for key, check in getattr(self, "tweak_checks", {}).items():
            check.blockSignals(True)
            check.setChecked(self.store.active(key))
            check.blockSignals(False)

    def _change_tweak(self, tweak_id: str, enabled: bool, checkbox: GlassSwitch | None = None) -> None:
        if tweak_id == "power_plan" and enabled:
            answer = QMessageBox.question(self, "Высокая производительность", "План может увеличить энергопотребление и нагрев. На ноутбуке рекомендуется включать его при подключённом зарядном устройстве. Продолжить?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                if checkbox:
                    checkbox.blockSignals(True); checkbox.setChecked(False); checkbox.blockSignals(False)
                return
        try:
            self.optimizer.set_tweak(tweak_id, enabled)
            self._toast("Настройка применена" if enabled else "Исходное значение восстановлено")
        except Exception as exc:
            if checkbox:
                checkbox.blockSignals(True); checkbox.setChecked(not enabled); checkbox.blockSignals(False)
            QMessageBox.warning(self, "Не удалось применить настройку", str(exc))
        self._update_tweak_count()

    def apply_recommended(self, from_home: bool = False) -> None:
        if not IS_WINDOWS:
            QMessageBox.information(self, "Windows-твики", "Системные настройки ForGo можно применять только в Windows 10/11.")
            return
        # The recommended profile deliberately avoids power-plan changes by default.
        # Power plan is offered separately with explicit confirmation due to heat/power trade-offs.
        answer = QMessageBox.question(
            self, "Рекомендуемый профиль ForGo",
            "Будут включены настройки, отмеченные ForGo как рекомендуемые. Часть записи клипов отключится, если этот пункт входит в профиль. План питания не изменится автоматически. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes: return
        errors: list[str] = []
        recommended_ids = [item["id"] for item in TWEAK_CATALOG if item["recommended"] and item["id"] in REGISTRY_TWEAKS]
        for tweak_id in recommended_ids:
            try: self.optimizer.set_tweak(tweak_id, True)
            except Exception as exc: errors.append(str(exc))
        self._sync_tweak_ui()
        self._update_tweak_count()
        if errors:
            QMessageBox.warning(self, "Часть настроек не применена", "\n".join(errors))
        else:
            selected_name = GAME_CATALOG.get(self.store.data.get("selected_game", "fortnite"), GAME_CATALOG["fortnite"])["name"]
            self._toast(f"Рекомендуемые настройки Windows применены для профиля {selected_name}")
        if from_home:
            self._apply_nav("boost")

    def restore_all(self) -> None:
        if not IS_WINDOWS:
            QMessageBox.information(self, "Восстановление", "В этой системе нет применённых твиков Windows.")
            return
        answer = QMessageBox.question(self, "Восстановить настройки", "ForGo вернёт сохранённые исходные значения для активных твиков. Продолжить?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes: return
        errors = self.optimizer.restore_all()
        self._sync_tweak_ui()
        self._update_tweak_count()
        if errors:
            QMessageBox.warning(self, "Не все значения восстановлены", "\n".join(errors))
        else:
            self._toast("Исходные значения восстановлены")

    def _launch_game(self) -> None:
        game_id = str(self.store.data.get("selected_game", "fortnite"))
        ok, message = launch_game(game_id)
        game_name = GAME_CATALOG.get(game_id, GAME_CATALOG["fortnite"])["name"]
        if ok:
            self._toast(message)
        else:
            QMessageBox.warning(self, f"Запуск {game_name}", message)

    def _apply_selected_game_profile(self) -> None:
        game_name = GAME_CATALOG.get(self.store.data.get("selected_game", "fortnite"), GAME_CATALOG["fortnite"])["name"]
        self._apply_nav("boost")
        self.apply_recommended(from_home=False)

    def _flush_dns(self) -> None:
        if not IS_WINDOWS:
            QMessageBox.information(self, "DNS", "Очистка DNS-кэша через ForGo доступна в Windows.")
            return
        answer = QMessageBox.question(self, "Очистить DNS-кэш", "Выполнить ipconfig /flushdns? Это безопасная диагностика; она обычно не снижает ping в активном матче.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
        if answer != QMessageBox.StandardButton.Yes: return
        try:
            p = subprocess.run(["ipconfig", "/flushdns"], capture_output=True, text=True, timeout=12, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            output = (p.stdout or p.stderr).strip()
            if p.returncode == 0:
                self._toast("DNS-кэш очищен")
            else:
                QMessageBox.warning(self, "DNS", output or "Не удалось очистить DNS-кэш.")
        except Exception as exc:
            QMessageBox.warning(self, "DNS", str(exc))

    def _clean_temp(self) -> None:
        temp_dir = Path(tempfile.gettempdir())
        answer = QMessageBox.question(self, "Очистить временные файлы", f"Удалить файлы старше 24 часов из:\n{temp_dir}\n\nЗанятые файлы будут пропущены. Это освобождает место, но не гарантирует прирост FPS.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes: return
        cutoff = time.time() - 24 * 60 * 60
        removed = 0
        bytes_freed = 0
        skipped = 0
        try:
            for item in temp_dir.iterdir():
                try:
                    if item.stat().st_mtime >= cutoff: continue
                    if item.is_file() or item.is_symlink():
                        size = item.stat().st_size
                        item.unlink()
                        removed += 1; bytes_freed += size
                    elif item.is_dir():
                        # Only remove directory when every entry is older than the cutoff; skip anything busy.
                        for base, dirs, files in os.walk(item, topdown=False):
                            for name in files:
                                f = Path(base) / name
                                try:
                                    if f.stat().st_mtime < cutoff:
                                        size = f.stat().st_size; f.unlink(); removed += 1; bytes_freed += size
                                except OSError: skipped += 1
                            for name in dirs:
                                d = Path(base) / name
                                try: d.rmdir()
                                except OSError: pass
                        try: item.rmdir()
                        except OSError: pass
                except OSError:
                    skipped += 1
            mb = bytes_freed / (1024 * 1024)
            QMessageBox.information(self, "Очистка завершена", f"Удалено файлов: {removed}\nОсвобождено: {mb:.1f} MB\nПропущено занятых/недоступных объектов: {skipped}")
        except Exception as exc:
            QMessageBox.warning(self, "Очистка", str(exc))

    def _toggle_priority(self) -> None:
        self.priority_active = not self.priority_active
        if self.priority_active:
            self.priority_button.setText("Выключить приоритет")
            self._maintain_priority()
            self._toast(f"Приоритет Above normal включён для {GAME_CATALOG.get(self.store.data.get('selected_game', 'fortnite'), GAME_CATALOG['fortnite'])['name']}, если игра запущена")
        else:
            self._restore_priorities()
            self.priority_button.setText("Включить приоритет")
            self._toast("Приоритеты процессов восстановлены")

    def _maintain_priority(self) -> None:
        if not self.priority_active or not IS_WINDOWS:
            return
        game_id = str(self.store.data.get("selected_game", "fortnite"))
        target_names = {name.lower() for name in GAME_CATALOG.get(game_id, GAME_CATALOG["fortnite"]).get("process_names", [])}
        if not target_names:
            return
        try:
            for proc in psutil.process_iter(["pid", "name"]):
                name = (proc.info.get("name") or "").lower()
                if name in target_names:
                    pid = proc.info["pid"]
                    if pid not in self.original_priorities:
                        try: self.original_priorities[pid] = proc.nice()
                        except Exception: continue
                    try: proc.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
                    except Exception: pass
        except Exception:
            pass

    def _restore_priorities(self) -> None:
        for pid, priority in list(self.original_priorities.items()):
            try: psutil.Process(pid).nice(priority)
            except Exception: pass
        self.original_priorities.clear()

    def _toast(self, message: str) -> None:
        # Lightweight in-window status line, not a blocking dialog.
        if not hasattr(self, "toast_label"):
            self.toast_label = QLabel(self.centralWidget())
            self.toast_label.setStyleSheet("background:#251b37;border:1px solid #63458c;border-radius:10px;color:#e4d8ff;font-size:12px;font-weight:600;padding:11px 15px;")
            self.toast_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.toast_label.adjustSize()
        self.toast_label.setText(message)
        self.toast_label.adjustSize()
        self.toast_label.move(max(15, self.width() - self.toast_label.width() - 22), self.height() - self.toast_label.height() - 20)
        self.toast_label.show(); self.toast_label.raise_()
        QTimer.singleShot(2800, self.toast_label.hide)

    def _toggle_maximize(self) -> None:
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _start_drag(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _do_drag(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton and not self.isMaximized():
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._restore_priorities()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(CSS)
    app.setFont(QFont("Segoe UI", 10))
    icon_path = Path(__file__).parent / "assets" / "ForGo.ico"
    if icon_path.exists(): app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
