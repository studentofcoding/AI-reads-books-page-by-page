import sys
from pathlib import Path
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import subprocess
import psutil
import os

class AppReloader(FileSystemEventHandler):
    def __init__(self):
        self.process = None
        self.last_modified = 0
        
    def run_app(self):
        # Kill existing process if it exists
        if self.process:
            try:
                parent = psutil.Process(self.process.pid)
                for child in parent.children(recursive=True):
                    child.kill()
                parent.kill()
            except:
                pass
        
        # Start new process
        print("\n🔄 Starting application...")
        self.process = subprocess.Popen([sys.executable, "pdf_analyzer_gui.py"])
        
    def on_modified(self, event):
        if event.src_path.endswith('.py'):
            current_time = time.time()
            if current_time - self.last_modified > 1:  # Debounce
                self.last_modified = current_time
                print(f"\n📝 Detected changes in {Path(event.src_path).name}")
                self.run_app()

def main():
    # Initial run
    reloader = AppReloader()
    reloader.run_app()
    
    # Set up file watcher
    observer = Observer()
    observer.schedule(reloader, path=".", recursive=False)
    observer.start()
    
    print("\n👀 Watching for file changes... (Ctrl+C to stop)")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Stopping development server...")
        observer.stop()
        if reloader.process:
            try:
                parent = psutil.Process(reloader.process.pid)
                for child in parent.children(recursive=True):
                    child.kill()
                parent.kill()
            except:
                pass
    
    observer.join()

if __name__ == "__main__":
    main() 