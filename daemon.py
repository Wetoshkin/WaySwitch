import evdev
from evdev import ecodes, InputDevice, UInput
import time
import subprocess
import json
import os

class WaylandSwitcher:
    def __init__(self):
        self.word_buffer = []
        self.keyboard = self.get_keyboard()
        if not self.keyboard:
            raise Exception("Клавиатура не найдена!")
        
        # Загружаем базу триграмм
        self.trigrams = self.load_trigrams()
        
        # Создаем виртуальное устройство
        self.ui = UInput.from_device(self.keyboard, name="GnomeSwitcher-Virtual")
        
    def load_trigrams(self):
        try:
            path = os.path.join(os.path.dirname(__file__), 'trigrams.json')
            if os.path.exists(path):
                with open(path, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Ошибка загрузки триграмм: {e}")
        return {"en_to_ru": [], "ru_to_en": []}

    def get_keyboard(self):
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        for device in devices:
            if "keyboard" in device.name.lower() and "GnomeSwitcher" not in device.name:
                return device
        return None

    def scancode_to_char(self, scancode):
        mapping = {
            ecodes.KEY_Q: 'q', ecodes.KEY_W: 'w', ecodes.KEY_E: 'e', ecodes.KEY_R: 'r',
            ecodes.KEY_T: 't', ecodes.KEY_Y: 'y', ecodes.KEY_U: 'u', ecodes.KEY_I: 'i',
            ecodes.KEY_O: 'o', ecodes.KEY_P: 'p', ecodes.KEY_A: 'a', ecodes.KEY_S: 's',
            ecodes.KEY_D: 'd', ecodes.KEY_F: 'f', ecodes.KEY_G: 'g', ecodes.KEY_H: 'h',
            ecodes.KEY_J: 'j', ecodes.KEY_K: 'k', ecodes.KEY_L: 'l', ecodes.KEY_Z: 'z',
            ecodes.KEY_X: 'x', ecodes.KEY_C: 'c', ecodes.KEY_V: 'v', ecodes.KEY_B: 'b',
            ecodes.KEY_N: 'n', ecodes.KEY_M: 'm', ecodes.KEY_SEMICOLON: ';', 
            ecodes.KEY_APOSTROPHE: "'", ecodes.KEY_COMMA: ',', ecodes.KEY_DOT: '.',
            ecodes.KEY_SLASH: '/', ecodes.KEY_LEFTBRACE: '[', ecodes.KEY_RIGHTBRACE: ']'
        }
        return mapping.get(scancode, '')

    def change_system_layout(self):
        try:
            cmd_get = "gsettings get org.gnome.desktop.input-sources current"
            current = int(subprocess.check_output(cmd_get, shell=True).decode().strip().split()[-1])
            cmd_list = "gsettings get org.gnome.desktop.input-sources sources"
            sources = subprocess.check_output(cmd_list, shell=True).decode().strip()
            num_sources = sources.count('(')
            next_layout = (current + 1) % num_sources
            subprocess.run(f"gsettings set org.gnome.desktop.input-sources current {next_layout}", shell=True)
        except:
            pass

    def is_likely_wrong_layout(self):
        if len(self.word_buffer) < 3: return False
        last_chars = "".join([self.scancode_to_char(s) for s in self.word_buffer[-3:]])
        # Если находим запрещенную триграмму - возвращаем True
        return last_chars in self.trigrams["en_to_ru"]

    def fix_word(self):
        if not self.word_buffer: return
        for _ in range(len(self.word_buffer)):
            self.ui.write(ecodes.EV_KEY, ecodes.KEY_BACKSPACE, 1)
            self.ui.write(ecodes.EV_KEY, ecodes.KEY_BACKSPACE, 0)
            self.ui.syn()
            time.sleep(0.005)
        self.change_system_layout()
        for scancode in self.word_buffer:
            self.ui.write(ecodes.EV_KEY, scancode, 1)
            self.ui.write(ecodes.EV_KEY, scancode, 0)
            self.ui.syn()
            time.sleep(0.005)
        self.word_buffer = []

    def run(self):
        self.keyboard.grab()
        try:
            for event in self.keyboard.read_loop():
                if event.type == ecodes.EV_KEY:
                    data = evdev.categorize(event)
                    if data.keystate == 1: # Key Down
                        if data.scancode == ecodes.KEY_F12:
                            self.fix_word()
                            continue
                        if data.scancode in [ecodes.KEY_SPACE, ecodes.KEY_ENTER]:
                            self.word_buffer = []
                        elif data.scancode == ecodes.KEY_BACKSPACE:
                            if self.word_buffer: self.word_buffer.pop()
                        else:
                            self.word_buffer.append(data.scancode)
                            if self.is_likely_wrong_layout():
                                self.fix_word()
                                continue
                        self.ui.write(ecodes.EV_KEY, data.scancode, 1)
                        self.ui.syn()
                    elif data.keystate == 0: # Key Up
                        self.ui.write(ecodes.EV_KEY, data.scancode, 0)
                        self.ui.syn()
        finally:
            self.keyboard.ungrab()

if __name__ == "__main__":
    switcher = WaylandSwitcher()
    switcher.run()
