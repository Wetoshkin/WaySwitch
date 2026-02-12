import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio

class SwitcherWindow(Gtk.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title("GnomeSwitcher Настройки")
        self.set_default_size(400, 300)

        # Основной контейнер
        view = Adw.StatusPage()
        view.set_title("GnomeSwitcher")
        view.set_description("Аналог Punto Switcher для Wayland")
        view.set_icon_name("preferences-desktop-keyboard")

        # Список настроек
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        
        group = Adw.PreferencesGroup()
        group.set_title("Основные настройки")
        
        # Переключатель автосмены
        auto_switch = Adw.ActionRow()
        auto_switch.set_title("Автоматически исправлять")
        auto_switch.set_subtitle("Менять раскладку при обнаружении ошибок")
        
        switch = Gtk.Switch()
        switch.set_active(True)
        switch.set_valign(Gtk.Align.CENTER)
        auto_switch.add_suffix(switch)
        group.add(auto_switch)

        # Настройка хоткея
        hotkey_row = Adw.ActionRow()
        hotkey_row.set_title("Клавиша ручного исправления")
        hotkey_row.set_subtitle("Нажмите эту клавишу, чтобы исправить последнее слово")
        
        hotkey_label = Gtk.Label(label="F12")
        hotkey_label.add_css_class("card") # Стиль кнопки
        hotkey_row.add_suffix(hotkey_label)
        group.add(hotkey_row)

        # Автозагрузка
        autostart_row = Adw.ActionRow()
        autostart_row.set_title("Запускать при старте системы")
        
        as_switch = Gtk.Switch()
        as_switch.set_active(True)
        as_switch.set_valign(Gtk.Align.CENTER)
        autostart_row.add_suffix(as_switch)
        group.add(autostart_row)

        # Сборка интерфейса
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.append(view)
        
        settings_list = Gtk.ListBox()
        settings_list.set_selection_mode(Gtk.SelectionMode.NONE)
        settings_list.add_css_class("boxed-list")
        settings_list.append(group)
        
        # Обертка для отступов
        clamp = Adw.Clamp()
        clamp.set_child(settings_list)
        
        page.append(clamp)
        self.set_child(page)

class SwitcherApp(Adw.Application):
    def __init__(self, **kwargs):
        super().__init__(application_id='ru.siberia.GnomeSwitcher',
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = SwitcherWindow(application=self)
        win.present()

if __name__ == "__main__":
    app = SwitcherApp()
    app.run(None)
