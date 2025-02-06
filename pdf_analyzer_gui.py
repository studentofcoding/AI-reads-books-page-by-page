from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                            QProgressBar, QTextEdit, QFileDialog, QGroupBox,
                            QSpinBox, QMessageBox, QCheckBox)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QIcon
import sys
import read_books
from pathlib import Path
from queue import Queue
from datetime import datetime
from PyPDF2 import PdfReader
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import black, blue, lightgrey, grey
import re
from gtts import gTTS
import PyPDF2

class AnalysisWorker(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(bool)  # True if successful, False if error
    error = pyqtSignal(str)

    def __init__(self, pdf_path, test_pages, interval):
        super().__init__()
        self.pdf_path = pdf_path
        self.test_pages = test_pages
        self.interval = interval
        self.running = True

    def run(self):
        try:
            # Set up progress callback
            def update_progress(value):
                if not self.running:
                    raise KeyboardInterrupt("Analysis stopped by user")
                self.progress.emit(int(value))

            read_books.set_progress_callback(update_progress)

            # Redirect stdout to our log
            class StreamWrapper:
                def __init__(self, signal, worker):
                    self.signal = signal
                    self.worker = worker

                def write(self, text):
                    if self.worker.running:
                        self.signal.emit(text)

                def flush(self):
                    pass

            sys.stdout = StreamWrapper(self.log, self)

            # Run analysis
            sys.argv = [
                'read_books.py',
                self.pdf_path,
                '--test-pages', str(self.test_pages),
                '--interval', str(self.interval)
            ]
            
            read_books.main()
            
            if self.running:
                self.finished.emit(True)
            else:
                self.finished.emit(False)
            
        except KeyboardInterrupt:
            self.finished.emit(False)
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit(False)
        finally:
            # Restore stdout
            sys.stdout = sys.__stdout__

    def stop(self):
        self.running = False

class AudioConversionWorker(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal(bool)
    error = pyqtSignal(str)
    
    def __init__(self, pdf_path):
        super().__init__()
        self.pdf_path = pdf_path
        
    def run(self):
        try:
            # Extract text from PDF
            with open(self.pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                total_pages = len(pdf_reader.pages)
                text_content = []
                
                for i, page in enumerate(pdf_reader.pages):
                    text_content.append(page.extract_text())
                    self.progress.emit(int((i + 1) / total_pages * 50))  # First 50% for extraction
                    
            full_text = ' '.join(text_content)
            
            # Convert to audio
            self.log.emit("\n🔊 Converting text to speech...")
            audio_path = Path(self.pdf_path).parent / f"{Path(self.pdf_path).stem}.mp3"
            
            tts = gTTS(text=full_text, lang='en', slow=False)
            tts.save(str(audio_path))
            
            self.progress.emit(100)
            self.log.emit(f"\n🎵 Audio book saved to: {audio_path}")
            self.finished.emit(True)
            
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit(False)

    def stop(self):
        self.running = False

class PDFAnalyzerGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.total_pages = 0
        self.current_summary_path = None  # Track current summary path
        self.current_pdf_path = None  # Track current PDF path
        self.initUI()

    def update_config_based_on_pdf(self, pdf_path):
        """Update configuration values based on PDF total pages"""
        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PdfReader(file)
                self.total_pages = len(pdf_reader.pages)
                
                if self.test_pages_enabled.isChecked():
                    # Calculate default test pages (1/3 of total, max 60)
                    default_test_pages = min(self.total_pages // 3, 60)
                    # Calculate default interval (1/3 of test pages, max 20)
                    default_interval = min(default_test_pages // 3, 20)
                    
                    # Update spinbox values
                    self.test_pages.setValue(default_test_pages)
                    self.interval.setValue(default_interval)
                else:
                    # If test pages disabled, set interval to 1/10 of total pages (max 20)
                    default_interval = min(self.total_pages // 10, 20)
                    self.interval.setValue(default_interval)
                
                # Update total pages label
                self.total_pages_label.setText(f"Total Pages: {self.total_pages}")
                
        except Exception as e:
            self.log_text.append(f"Error reading PDF: {str(e)}")

    def initUI(self):
        self.setWindowTitle('PDF Book Analyzer')
        self.setGeometry(100, 100, 900, 700)
        
        # Create central widget and main layout
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # File Selection Section
        file_group = QGroupBox("File Selection")
        file_layout = QHBoxLayout()
        
        self.file_path = QLineEdit()
        self.file_path.setPlaceholderText("Select PDF file...")
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_file)
        
        file_layout.addWidget(self.file_path)
        file_layout.addWidget(browse_btn)
        file_group.setLayout(file_layout)
        
        # Configuration Section
        config_group = QGroupBox("Configuration")
        config_layout = QVBoxLayout()
        
        # Total Pages Info
        total_pages_layout = QHBoxLayout()
        self.total_pages_label = QLabel("Total Pages: 0")
        self.total_pages_label.setStyleSheet("font-weight: bold;")
        total_pages_layout.addWidget(self.total_pages_label)
        total_pages_layout.addStretch()
        config_layout.addLayout(total_pages_layout)
        
        # Test Pages and Interval in one row
        controls_layout = QHBoxLayout()
        
        # Test Pages
        test_layout = QVBoxLayout()
        test_header = QHBoxLayout()
        test_label = QLabel("Test Pages:")
        self.test_pages_enabled = QCheckBox()
        self.test_pages_enabled.setChecked(False)
        self.test_pages_enabled.stateChanged.connect(self.toggle_test_pages)
        test_header.addWidget(test_label)
        test_header.addWidget(self.test_pages_enabled)
        test_header.addStretch()
        
        self.test_pages = QSpinBox()
        self.test_pages.setRange(0, 9999)
        self.test_pages.setValue(60)
        self.test_pages.setEnabled(False)
        self.test_pages.setToolTip("Disabled - Will process all pages")
        test_layout.addLayout(test_header)
        test_layout.addWidget(self.test_pages)
        controls_layout.addLayout(test_layout)
        
        # Interval
        interval_layout = QVBoxLayout()
        interval_label = QLabel("Analysis Interval:")
        self.interval = QSpinBox()
        self.interval.setRange(0, 100)
        self.interval.setValue(20)
        self.interval.setToolTip("0 to disable\nDefault: 1/10 of total pages (max 20)")
        interval_layout.addWidget(interval_label)
        interval_layout.addWidget(self.interval)
        controls_layout.addLayout(interval_layout)
        
        config_layout.addLayout(controls_layout)
        config_group.setLayout(config_layout)
        
        # Log Section
        log_group = QGroupBox("Processing Log")
        log_layout = QVBoxLayout()
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        
        # Progress Section
        progress_layout = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignCenter)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.status_label)
        
        # Control Buttons
        button_layout = QHBoxLayout()
        self.start_button = QPushButton("Start Analysis")
        self.start_button.clicked.connect(self.start_analysis)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_analysis)
        self.stop_button.setEnabled(False)
        
        self.convert_button = QPushButton("Convert to PDF")
        self.convert_button.clicked.connect(self.convert_current_to_pdf)
        self.convert_button.setEnabled(False)  # Initially disabled
        
        # Add audio conversion button
        self.audio_button = QPushButton("Convert to Audio")
        self.audio_button.clicked.connect(self.convert_to_audio)
        self.audio_button.setEnabled(False)  # Initially disabled
        
        button_layout.addWidget(self.start_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addWidget(self.convert_button)
        button_layout.addWidget(self.audio_button)
        
        # Add all sections to main layout
        layout.addWidget(file_group)
        layout.addWidget(config_group)
        layout.addWidget(log_group)
        layout.addLayout(progress_layout)
        layout.addLayout(button_layout)
        
        # Style
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e1e;
            }
            QWidget {
                background-color: #1e1e1e;
                color: #ffffff;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #333333;
                border-radius: 6px;
                margin-top: 6px;
                padding-top: 10px;
                color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 7px;
                padding: 0px 5px 0px 5px;
                color: #ffffff;
            }
            QPushButton {
                background-color: #0078D4;
                color: white;
                border: none;
                padding: 5px 15px;
                border-radius: 4px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #106EBE;
            }
            QPushButton:disabled {
                background-color: #333333;
                color: #666666;
            }
            QProgressBar {
                border: 1px solid #333333;
                border-radius: 4px;
                text-align: center;
                color: #ffffff;
                background-color: #2d2d2d;
            }
            QProgressBar::chunk {
                background-color: #0078D4;
            }
            QTextEdit {
                border: 1px solid #333333;
                border-radius: 4px;
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QLineEdit {
                border: 1px solid #333333;
                border-radius: 4px;
                padding: 5px;
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QSpinBox {
                border: 1px solid #333333;
                border-radius: 4px;
                padding: 5px;
                background-color: #2d2d2d;
                color: #ffffff;
            }
            QLabel {
                color: #ffffff;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                background-color: #333333;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #404040;
            }
            QLabel[font-weight="bold"] {
                color: #ffffff;
                font-weight: bold;
                font-size: 12px;
            }
            QCheckBox {
                color: #ffffff;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                background-color: #2d2d2d;
                border: 1px solid #333333;
                border-radius: 3px;
            }
            QCheckBox::indicator:checked {
                background-color: #0078D4;
                image: url(data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='18' height='18' viewBox='0 0 24 24'%3E%3Cpath fill='white' d='M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z'/%3E%3C/svg%3E);
            }
            QCheckBox::indicator:hover {
                border-color: #0078D4;
            }
            QSpinBox:disabled {
                background-color: #1e1e1e;
                color: #666666;
                border-color: #222222;
            }
            QPushButton#audioButton {
                background-color: #2ecc71;
            }
            QPushButton#audioButton:hover {
                background-color: #27ae60;
            }
        """)

    def browse_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select PDF File",
            str(Path.home()),
            "PDF Files (*.pdf)"
        )
        if filename:
            self.file_path.setText(str(Path(filename).absolute()))
            # Update configuration when new file is selected
            self.update_config_based_on_pdf(filename)

    def toggle_test_pages(self, state):
        """Enable/disable test pages spinbox"""
        self.test_pages.setEnabled(state == Qt.Checked)
        if state == Qt.Checked:
            self.test_pages.setToolTip("Default: 1/3 of total pages (max 60)")
        else:
            self.test_pages.setToolTip("Disabled - Will process all pages")

    def start_analysis(self):
        if not self.file_path.text():
            self.log_text.append("Please select a PDF file first.")
            return
        
        # Ensure the path is absolute and exists
        pdf_path = Path(self.file_path.text()).absolute()
        if not pdf_path.exists():
            self.log_text.append(f"Error: File not found: {pdf_path}")
            return
        
        # Check if file was already analyzed
        pdf_stem = pdf_path.stem
        summaries_dir = Path("book_analysis/summaries")
        final_summaries = list(summaries_dir.glob(f"{pdf_stem}_final_*.md"))
        
        if final_summaries:
            latest_summary = max(final_summaries, key=lambda p: p.stat().st_mtime)
            self.log_text.clear()
            self.log_text.append(f"📝 This file has already been analyzed.")
            self.log_text.append(f"Last analysis: {datetime.fromtimestamp(latest_summary.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}\n")
            
            # Ask user if they want to reanalyze
            reply = QMessageBox.question(
                self, 
                'File Already Analyzed',
                'This file has already been analyzed. Would you like to:\n\n'
                '- View the last analysis (No)\n'
                '- Run a new analysis (Yes)',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.No:
                # Show last analysis
                try:
                    with open(latest_summary, 'r', encoding='utf-8') as f:
                        content = f.read()
                        self.current_summary_path = latest_summary  # Store current summary path
                        
                        # Extract key sections for simplified summary
                        self.log_text.append("="*50)
                        self.log_text.append("📚 LAST ANALYSIS SUMMARY")
                        self.log_text.append("="*50 + "\n")
                        
                        # Find 5-Minute Summary section
                        lines = content.split('\n')
                        in_summary = False
                        summary_text = []
                        
                        for line in lines:
                            if line.startswith('### 5-Minute Summary'):
                                in_summary = True
                                continue
                            elif in_summary and line.startswith('###'):
                                break
                            elif in_summary and line.strip():
                                summary_text.append(line.strip())
                        
                        # Show summary
                        if summary_text:
                            self.log_text.append("5-Minute Summary:")
                            self.log_text.append("\n".join(summary_text))
                        else:
                            self.log_text.append("No quick summary available.")
                        
                        self.log_text.append(f"\n📄 Full analysis available at: {latest_summary}")
                        self.log_text.append("="*50)
                        
                        # Check if PDF version exists
                        if not self.check_pdf_version(latest_summary):
                            self.log_text.append("\n💡 PDF version not found. Click 'Convert to PDF' to create one.")
                            self.convert_button.setEnabled(True)
                            self.audio_button.setEnabled(False)
                        else:
                            pdf_path = latest_summary.parent / f"{latest_summary.stem}.pdf"
                            self.current_pdf_path = pdf_path  # Store PDF path
                            self.log_text.append(f"\n📄 PDF version available at: {pdf_path}")
                            self.convert_button.setEnabled(False)
                            
                            # Check if audio version exists
                            audio_path = pdf_path.parent / f"{pdf_path.stem}.mp3"
                            if audio_path.exists():
                                self.log_text.append(f"\n🎵 Audio version available at: {audio_path}")
                                self.audio_button.setEnabled(False)
                            else:
                                self.log_text.append("\n🔊 PDF can be converted to audio. Click 'Convert to Audio' to create one.")
                                self.audio_button.setEnabled(True)
                        
                        self.status_label.setText("Showing last analysis")
                        return
                        
                except Exception as e:
                    self.log_text.append(f"\nError reading summary: {str(e)}")
                    # Continue with new analysis if there's an error reading the old one
        
        # Proceed with new analysis
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("Processing...")
        self.log_text.clear()
        
        # Create and start worker thread
        test_pages = 0 if not self.test_pages_enabled.isChecked() else self.test_pages.value()
        self.worker = AnalysisWorker(
            str(pdf_path),
            test_pages,
            self.interval.value()
        )
        
        self.worker.progress.connect(self.update_progress)
        self.worker.log.connect(self.update_log)
        self.worker.finished.connect(self.analysis_finished)
        self.worker.error.connect(self.handle_error)
        
        self.worker.start()

    def stop_analysis(self):
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.status_label.setText("Stopping...")
            self.stop_button.setEnabled(False)
            self.worker.stop()
            # Force quit after 3 seconds if worker hasn't stopped
            QTimer.singleShot(3000, self.force_stop)

    def force_stop(self):
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()
            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            self.status_label.setText("Analysis stopped forcefully")
            self.log_text.append("\n⚠️ Analysis terminated forcefully")

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def update_log(self, text):
        self.log_text.append(text)

    def convert_md_to_pdf(self, md_file: Path):
        """Convert markdown file to PDF using reportlab with markdown formatting"""
        try:
            pdf_file = md_file.parent / f"{md_file.stem}.pdf"
            
            # Read markdown content
            with open(md_file, 'r', encoding='utf-8') as f:
                markdown_content = f.read()
            
            # Create PDF document
            doc = SimpleDocTemplate(
                str(pdf_file),
                pagesize=letter,
                rightMargin=72,
                leftMargin=72,
                topMargin=72,
                bottomMargin=72
            )
            
            # Create styles
            styles = getSampleStyleSheet()
            title_style = ParagraphStyle(
                'CustomTitle',
                parent=styles['Heading1'],
                fontSize=16,
                spaceAfter=30,
                textColor=black
            )
            normal_style = ParagraphStyle(
                'CustomBody',
                parent=styles['Normal'],
                fontSize=11,
                spaceAfter=12,
                textColor=black
            )
            list_style = ParagraphStyle(
                'CustomList',
                parent=styles['Normal'],
                fontSize=11,
                spaceAfter=12,
                leftIndent=20,
                bulletIndent=10,
                textColor=black
            )
            
            # Process content
            story = []
            
            # Add title
            title = f"Book Analysis: {Path(self.file_path.text()).name}"
            story.append(Paragraph(title, title_style))
            
            def process_markdown_text(text):
                """Process markdown formatting within text"""
                # Bold
                text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
                # Italic
                text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
                # Code
                text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
                # Links
                text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<link href="\2">\1</link>', text)
                return text
            
            def process_table(table_lines):
                """Process markdown table into reportlab table"""
                if not table_lines:
                    return None
            
                # Parse header separator
                separator = table_lines[1]
                alignments = []
                for col in separator.split('|')[1:-1]:
                    col = col.strip()
                    if col.startswith(':') and col.endswith(':'):
                        alignments.append('CENTER')
                    elif col.endswith(':'):
                        alignments.append('RIGHT')
                    else:
                        alignments.append('LEFT')
            
                # Process rows
                table_data = []
                for line in [table_lines[0]] + table_lines[2:]:  # Skip separator line
                    if line.strip():
                        row = [cell.strip() for cell in line.split('|')[1:-1]]
                        # Process markdown formatting in cells
                        row = [process_markdown_text(cell) for cell in row]
                        table_data.append(row)
            
                if not table_data:
                    return None
            
                # Create table
                table = Table(table_data)
            
                # Style the table
                style = [
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),  # Header font
                    ('FONTSIZE', (0, 0), (-1, -1), 10),  # Font size
                    ('BOTTOMPADDING', (0, 0), (-1, 0), 12),  # Header padding
                    ('TOPPADDING', (0, 1), (-1, -1), 6),  # Cell padding
                    ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
                    ('GRID', (0, 0), (-1, -1), 0.5, grey),  # Grid lines
                    ('BACKGROUND', (0, 0), (-1, 0), lightgrey),  # Header background
                ]
            
                # Apply alignments
                for i, alignment in enumerate(alignments):
                    style.append(('ALIGN', (i, 0), (i, -1), alignment))
            
                table.setStyle(TableStyle(style))
                return table
            
            # Process markdown content
            lines = markdown_content.split('\n')
            current_text = []
            in_list = False
            list_items = []
            table_lines = []
            in_table = False
            
            for line in lines:
                # Table handling
                if line.strip().startswith('|'):
                    if not in_table:
                        # Process any pending text before table
                        if current_text:
                            story.append(Paragraph(process_markdown_text('\n'.join(current_text)), normal_style))
                            current_text = []
                        if list_items:
                            for item in list_items:
                                story.append(Paragraph(f"• {process_markdown_text(item)}", list_style))
                            list_items = []
                        in_table = True
                    table_lines.append(line)
                elif in_table:
                    # End of table
                    in_table = False
                    table = process_table(table_lines)
                    if table:
                        story.append(table)
                        story.append(Paragraph("<br/>", normal_style))  # Add spacing after table
                    table_lines = []
                    if line.strip():
                        current_text.append(line)
                # Headers
                elif line.startswith('#'):
                    # Process any pending text
                    if current_text:
                        story.append(Paragraph('\n'.join(current_text), normal_style))
                        current_text = []
                    if list_items:
                        for item in list_items:
                            story.append(Paragraph(f"• {process_markdown_text(item)}", list_style))
                        list_items = []
                    
                    header_text = line.lstrip('#').strip()
                    level = line.count('#')
                    header_style = ParagraphStyle(
                        f'CustomHeader{level}',
                        parent=styles[f'Heading{min(level, 3)}'],
                        fontSize=16 - (level * 2),
                        spaceAfter=12,
                        textColor=black
                    )
                    story.append(Paragraph(process_markdown_text(header_text), header_style))
                
                # Lists
                elif line.strip().startswith('- ') or line.strip().startswith('* '):
                    if current_text:
                        story.append(Paragraph('\n'.join(current_text), normal_style))
                        current_text = []
                    list_items.append(line.strip()[2:])
                    in_list = True
                
                # Empty lines
                elif line.strip() == '':
                    if current_text:
                        story.append(Paragraph(process_markdown_text('\n'.join(current_text)), normal_style))
                        current_text = []
                    if list_items:
                        for item in list_items:
                            story.append(Paragraph(f"• {process_markdown_text(item)}", list_style))
                        list_items = []
                        in_list = False
                
                # Horizontal rule
                elif line.strip() == '---':
                    if current_text:
                        story.append(Paragraph('\n'.join(current_text), normal_style))
                        current_text = []
                    story.append(Paragraph("<hr/>", normal_style))
                
                # Normal text
                else:
                    if in_list and not line.strip().startswith(('- ', '* ')):
                        in_list = False
                        for item in list_items:
                            story.append(Paragraph(f"• {process_markdown_text(item)}", list_style))
                        list_items = []
                    current_text.append(line)
            
            # Handle any remaining table
            if table_lines:
                table = process_table(table_lines)
                if table:
                    story.append(table)
                    story.append(Paragraph("<br/>", normal_style))
            
            # Add any remaining text
            if current_text:
                story.append(Paragraph(process_markdown_text('\n'.join(current_text)), normal_style))
            if list_items:
                for item in list_items:
                    story.append(Paragraph(f"• {process_markdown_text(item)}", list_style))
            
            # Add footer
            footer_text = f"\nGenerated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            story.append(Paragraph(footer_text, normal_style))
            
            # Build PDF
            doc.build(story)
            
            self.log_text.append(f"\n📄 PDF version saved to: {pdf_file}")
            self.current_pdf_path = pdf_file  # Store PDF path
            self.audio_button.setEnabled(True)  # Enable audio conversion
            return pdf_file
            
        except Exception as e:
            self.log_text.append(f"\n⚠️ Error converting to PDF: {str(e)}")
            return None

    def convert_current_to_pdf(self):
        """Convert current summary to PDF"""
        if self.current_summary_path:
            self.log_text.append("\n🔄 Converting analysis to PDF...")
            pdf_file = self.convert_md_to_pdf(Path(self.current_summary_path))
            if pdf_file:
                self.log_text.append("✅ Conversion complete!")
                self.convert_button.setEnabled(False)

    def check_pdf_version(self, md_path: Path) -> bool:
        """Check if PDF version exists for the markdown file"""
        pdf_path = md_path.parent / f"{md_path.stem}.pdf"
        return pdf_path.exists()

    def analysis_finished(self, success):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if success:
            self.status_label.setText("Analysis completed successfully!")
            
            # Find and read the latest final summary
            pdf_stem = Path(self.file_path.text()).stem
            summaries_dir = Path("book_analysis/summaries")
            final_summaries = list(summaries_dir.glob(f"{pdf_stem}_final_*.md"))
            
            if final_summaries:
                latest_summary = max(final_summaries, key=lambda p: p.stat().st_mtime)
                self.current_summary_path = latest_summary  # Store current summary path
                try:
                    with open(latest_summary, 'r', encoding='utf-8') as f:
                        content = f.read()
                        
                        # Extract key sections for simplified summary
                        self.log_text.append("\n" + "="*50)
                        self.log_text.append("📚 SIMPLIFIED SUMMARY")
                        self.log_text.append("="*50 + "\n")
                        
                        # Find 5-Minute Summary section
                        lines = content.split('\n')
                        in_summary = False
                        summary_text = []
                        
                        for line in lines:
                            if line.startswith('### 5-Minute Summary'):
                                in_summary = True
                                continue
                            elif in_summary and line.startswith('###'):
                                break
                            elif in_summary and line.strip():
                                summary_text.append(line.strip())
                        
                        # Show summary
                        if summary_text:
                            self.log_text.append("5-Minute Summary:")
                            self.log_text.append("\n".join(summary_text))
                        else:
                            self.log_text.append("No quick summary available.")
                        
                        self.log_text.append(f"\n📄 Full analysis saved to: {latest_summary}")
                        self.log_text.append("="*50)
                        
                        # Check if PDF version exists
                        if not self.check_pdf_version(latest_summary):
                            self.log_text.append("\n💡 PDF version not found. Click 'Convert to PDF' to create one.")
                            self.convert_button.setEnabled(True)
                            self.audio_button.setEnabled(False)
                        else:
                            pdf_path = latest_summary.parent / f"{latest_summary.stem}.pdf"
                            self.current_pdf_path = pdf_path  # Store PDF path
                            self.log_text.append(f"\n📄 PDF version available at: {pdf_path}")
                            self.convert_button.setEnabled(False)
                            self.audio_button.setEnabled(True)  # Enable audio conversion
                        
                except Exception as e:
                    self.log_text.append(f"\nError reading summary: {str(e)}")
        else:
            self.status_label.setText("Analysis stopped by user.")
            self.convert_button.setEnabled(False)
            self.audio_button.setEnabled(False)

    def handle_error(self, error_msg):
        self.log_text.append(f"\nError: {error_msg}")
        self.status_label.setText("Error occurred during analysis")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def convert_to_audio(self):
        """Convert PDF to audio book"""
        if not self.current_pdf_path:
            return
            
        try:
            self.log_text.append("\n🎵 Converting to audio book...")
            self.audio_button.setEnabled(False)
            self.status_label.setText("Converting to audio...")
            
            # Create audio worker to prevent UI freeze
            self.audio_worker = AudioConversionWorker(self.current_pdf_path)
            self.audio_worker.progress.connect(self.update_audio_progress)
            self.audio_worker.log.connect(self.update_log)
            self.audio_worker.finished.connect(self.audio_conversion_finished)
            self.audio_worker.error.connect(self.handle_error)
            
            self.audio_worker.start()
            
        except Exception as e:
            self.log_text.append(f"\n⚠️ Error starting audio conversion: {str(e)}")
            self.audio_button.setEnabled(True)
            
    def update_audio_progress(self, value):
        """Update progress bar for audio conversion"""
        self.progress_bar.setValue(value)
        
    def audio_conversion_finished(self, success):
        """Handle audio conversion completion"""
        self.audio_button.setEnabled(False)  # Disable after successful conversion
        if success:
            self.status_label.setText("Audio conversion completed!")
            # Check for audio file
            if self.current_pdf_path:
                audio_path = Path(self.current_pdf_path).parent / f"{Path(self.current_pdf_path).stem}.mp3"
                if audio_path.exists():
                    self.log_text.append(f"\n🎵 Audio version available at: {audio_path}")
        else:
            self.status_label.setText("Audio conversion failed")
            self.audio_button.setEnabled(True)  # Re-enable on failure

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # Modern look across platforms
    window = PDFAnalyzerGUI()
    window.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main() 