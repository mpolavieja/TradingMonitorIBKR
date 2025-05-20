import tkinter as tk
from tkinter import messagebox
import json


class ShortAvailabilityChecker:
    def __init__(self, master: tk.Tk):       
        self.master = master
        self.master.title("Short Availability Check")
        self.master.geometry("600x300")  # Set the window size to 500x300

        self.input_entry = tk.Text(master, height=3, width=70, wrap="none")
        self.input_entry.pack(side=tk.TOP)

        submit_button = tk.Button(master, text="Save", command=self.save_symbols)
        submit_button.pack(pady=5)

        check_button = tk.Button(master, text="Check Call Back", command=self._checkCallBack)
        check_button.pack(pady=5)
        self.checkCallBack = False

        check_button = tk.Button(master, text="Get Market Data", command=self.getMktData)
        check_button.pack(pady=5)

        self.load_symbols()

    
    def getMktData(self):
        pass

    def load_symbols(self):
        try:
            with open('symbols.json', 'r') as file:
                symbols = json.load(file)
                symbols_str = ', '.join(symbols)
                self.input_entry.delete(1.0, tk.END)
                self.input_entry.insert(1.0, symbols_str)
        except FileNotFoundError:
            messagebox.showwarning(title = "Load Symbols", message = "Symbols file not found.") # type: ignore  Library problem

    def save_symbols(self):
        symbols = self.readSymbols()
        if symbols:
            with open('symbols.json', 'w') as file:
                json.dump(symbols, file)
            messagebox.showinfo("Save Symbols", "Symbols saved successfully.") # type: ignore  Library problem
        else:
            messagebox.showwarning("Save Symbols", "No symbols to save.") # type: ignore  Library problem

    def _checkCallBack(self):
        self.checkCallBack = True

    def readSymbols(self) -> list[str]:
        # Get the input text, split by commas, strip whitespace, and convert to uppercase
        symbols = self.input_entry.get("1.0", "end-1c").split(',')
        symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]

        return symbols


if __name__ == '__main__':
    root = tk.Tk()
    app = ShortAvailabilityChecker(root)
    app.master.title("Short Availability Check -- PRUEBAS!!!!")
    app.master.configure(bg="yellow")  # Set the background color to yellow
    root.mainloop()
