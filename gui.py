#Riley O'Shea
#University of Colorado Colorado Springs
#7/29/25
#Graphical User Interface for user interaction with auditing tool

import tkinter as tk
import tkinter.ttk as tkk
from tkinter import messagebox, filedialog
import subprocess
import json, sys
import os
import re
from config.version import version

def dataReset():
    """Reset all data by running the dataReset script."""
    subprocess.run([sys.executable, "dataReset.py"])
    messagebox.showinfo("Reset", "Data has been reset successfully.")


def showAuditResults():
    """show a message box showing the count of videos with and without captions."""
    messagebox.showinfo("Audit", "Running audit, this may take a while depending on the number of videos.")
    subprocess.run([sys.executable, "runAudit.py"], capture_output=True, text=True)
    messagebox.showinfo("Audit Complete", "Audit completed successfully. Check the 'data' folder for results.")


def open_json_file():
    """
    Open the audited videos JSON in the system's default application.
    Tries both 'audited_videos.json' and 'auditedVideos.json' just in case.
    """
    # Try both common names
    candidates = [
        os.path.join("data", "audited_videos.json"),
        os.path.join("data", "auditedVideos.json"),
    ]
    file_path = next((p for p in candidates if os.path.exists(p)), None)

    if not file_path:
        messagebox.showerror("Error", "Could not find audited videos JSON in /data.")
        return

    # Normalize to an absolute path
    file_path = os.path.abspath(file_path)

    try:
        if sys.platform.startswith("win"):
            os.startfile(file_path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", file_path])
        else:
            subprocess.Popen(["xdg-open", file_path])
    except Exception as e:
        messagebox.showerror("Error", f"Failed to open file:\n{e}")


def runIndividualAudit(course_id):
    """
    args:
        course_id (str): The ID of the course to audit.
    Run an individual course aduit. Results will be saved in the 'data' folder.
    
    """
    subprocess.run([sys.executable, "individualAudit.py", course_id])
    messagebox.showinfo(
        "Audit Complete",
        f"Audit for course {course_id} completed successfully. Press 'View Results' to see the results.",
    )


def loadCanvasAPIToken():
    """Pull the Canvas API token from the config file."""
    config_path = os.path.join(os.path.dirname(__file__), "config", "canvasAPI.py")
    try:
        with open(config_path, "r") as f:
            for line in f:
                match = re.match(r"^CANVAS_API_TOKEN\s*=\s*['\"](.*)['\"]", line)
                if match:
                    return match.group(1)
    except Exception:
        pass
    return ""


def saveCanvasAPIToken(token):
    """
    args:
        token (str): The Canvas API token to save.
    Save the Canvas API token to the config file.
    """
    config_path = os.path.join(os.path.dirname(__file__), "config", "canvasAPI.py")
    try:
        with open(config_path, "r") as f:
            lines = f.readlines()
        with open(config_path, "w") as f:
            for line in lines:
                if line.startswith("CANVAS_API_TOKEN"):
                    f.write(f'CANVAS_API_TOKEN = "{token}"\n')
                else:
                    f.write(line)
        return True
    except Exception as e:
        messagebox.showerror("Error", f"Failed to save token: {e}")
        return False


def main():
    # GUI Setup
    root = tk.Tk()
    root.title(f"UCCS Closed Captioning Audit {version}")
    root.geometry("500x350")

    tk.Label(root, text="UCCS Closed Captioning Audit", font=("Arial", 20)).pack(pady=10)
    tk.Button(root, text="Run Complete Audit", command=showAuditResults).pack(pady=10)

    def promptIndividualAudit():
        popup = tk.Toplevel(root)
        popup.title("Individual Course Audit")
        popup.geometry("350x150")
        popup.transient(root)
        popup.grab_set()

        tk.Label(popup, text="Enter Course ID:").pack(pady=5)
        entry = tk.Entry(popup, width=30)
        entry.pack(pady=5)

        def confirm():
            course = entry.get().strip()
            if not course:
                messagebox.showerror("Error", "Please enter a course ID.")
                return
            popup.destroy()
            runIndividualAudit(course)

        tk.Button(popup, text="Confirm", command=confirm).pack(pady=5)
        tk.Button(popup, text="Cancel", command=popup.destroy).pack()
        popup.wait_window()

    def promptSettings():
        popup = tk.Toplevel(root)
        popup.title("Settings")
        popup.geometry("400x200")
        popup.transient(root)
        popup.grab_set()

        tk.Label(popup, text="Canvas API Token:").pack(pady=5)
        token_entry = tk.Entry(popup, width=50)
        token_entry.insert(0, loadCanvasAPIToken())
        token_entry.pack(pady=5)

        def save_and_close():
            new_token = token_entry.get().strip()
            if saveCanvasAPIToken(new_token):
                messagebox.showinfo("Settings", "Token saved successfully.")
                popup.destroy()

        tk.Button(popup, text="Save", command=save_and_close).pack(pady=5)
        tk.Button(popup, text="Cancel", command=popup.destroy).pack()
        popup.wait_window()

    # Main buttons
    tk.Button(root, text="Run Individual Course Audit", command=promptIndividualAudit).pack(pady=10)
    tk.Button(root, text="View Results", command=open_json_file).pack(pady=10)
    tk.Button(root, text="Settings", command=promptSettings).pack(pady=10)
    tk.Button(root,
              text="Reset Data (WARNING: All existing data will be lost!)",
              command=dataReset,
              bg="red", fg="white").pack(pady=10)

    root.mainloop()


if __name__ == "__main__":
    main()
