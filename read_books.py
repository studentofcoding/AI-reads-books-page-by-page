from pathlib import Path
from typing import Dict, Any, Optional
from pydantic import BaseModel
import json
from openai import OpenAI
from PyPDF2 import PdfReader
from termcolor import colored
from datetime import datetime
import shutil
import sys
import argparse
import pickle


# Configuration Constants
PDF_NAME = "dllm.pdf"  # Default value, can be overridden by command line
BASE_DIR = Path("book_analysis")
PDF_DIR = BASE_DIR / "pdfs"
KNOWLEDGE_DIR = BASE_DIR / "knowledge_bases"
SUMMARIES_DIR = BASE_DIR / "summaries"
PDF_PATH = PDF_DIR / PDF_NAME
OUTPUT_PATH = KNOWLEDGE_DIR / f"{PDF_NAME.replace('.pdf', '_knowledge.json')}"
ANALYSIS_INTERVAL = 5  # Set to None to skip interval analyses, or a number (e.g., 10) to generate analysis every N pages
MODEL = "gpt-4o-mini"
ANALYSIS_MODEL = "o1-mini"
TEST_PAGES = 20  # Set to None to process entire book
CONTEXT_WINDOW = 5  # Number of previous analyses to consider when generating new summary

# Add new constant for progress tracking
PROGRESS_DIR = BASE_DIR / "progress"

# Add these at the top with other imports
progress_callback = None

def set_progress_callback(callback):
    global progress_callback
    progress_callback = callback

def update_paths():
    """Update paths when PDF_NAME changes"""
    global PDF_PATH, OUTPUT_PATH
    PDF_PATH = PDF_DIR / PDF_NAME
    pdf_stem = Path(PDF_NAME).stem
    OUTPUT_PATH = KNOWLEDGE_DIR / f"{pdf_stem}_knowledge.json"

def main():
    args = parse_arguments()
    
    # Update configuration based on command line arguments
    global PDF_NAME
    
    # Use command-line argument if provided, otherwise use default PDF_NAME
    if args.pdf_name is not None:
        PDF_NAME = args.pdf_name
        update_paths()  # Update paths with new PDF_NAME

    setup_directories()
    client = OpenAI()
    
    # Load saved progress if exists
    start_page, knowledge_base, previous_analyses, last_analysis_count = load_progress()
    
    # If no progress found, start fresh
    if start_page == -1:
        knowledge_base = load_existing_knowledge()
        start_page = 0
        previous_analyses = []
        last_analysis_count = 0

    # Get actual page count first
    with open(PDF_PATH, 'rb') as file:
        pdf_reader = PdfReader(file)
        total_pages = len(pdf_reader.pages)
    
    # Adjust TEST_PAGES if it exceeds total pages
    TEST_PAGES = min(args.test_pages, total_pages) if args.test_pages > 0 else None
    ANALYSIS_INTERVAL = None if args.interval == 0 else args.interval
    
    # Process pages with progress tracking
    if TEST_PAGES is not None:
        end_page = min(TEST_PAGES, total_pages)
        knowledge_base, previous_analyses, last_analysis_count = process_pages(
            client, PDF_PATH, start_page, end_page,
            knowledge_base, previous_analyses, last_analysis_count
        )
        
        if end_page < total_pages:
            # Remove the input prompt and continue processing
            knowledge_base, previous_analyses, last_analysis_count = process_pages(
                client, PDF_PATH, end_page, total_pages,
                knowledge_base, previous_analyses, last_analysis_count
            )
    else:
        # Process all pages if TEST_PAGES is None
        print(colored(f"\n📚 Processing all {total_pages} pages...", "cyan"))
        knowledge_base, previous_analyses, last_analysis_count = process_pages(
            client, PDF_PATH, start_page, total_pages,
            knowledge_base, previous_analyses, last_analysis_count
        )
    
    # Always generate final analysis at the end
    print(colored(f"\n📊 All {total_pages} pages processed", "cyan"))
    final_summary = analyze_knowledge_base(client, knowledge_base, previous_analyses)
    save_summary(final_summary, is_final=True)
    
    # Clear progress file after successful completion
    clear_progress()
    
    print(colored("\n✨ Processing complete! ✨", "green", attrs=['bold']))

class PageContent(BaseModel):
    has_content: bool
    knowledge: list[str]


def load_or_create_knowledge_base() -> Dict[str, Any]:
    if Path(OUTPUT_PATH).exists():
        with open(OUTPUT_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_knowledge_base(knowledge_base: list[str]):
    output_path = KNOWLEDGE_DIR / f"{Path(PDF_NAME).stem}_knowledge.json"
    print(colored(f"💾 Saving knowledge base ({len(knowledge_base)} items)...", "blue"))
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({"knowledge": knowledge_base}, f, indent=2)

def process_page(client: OpenAI, page_text: str, current_knowledge: list[str], page_num: int) -> list[str]:
    print(colored(f"\n📖 Processing page {page_num + 1}...", "yellow"))
    
    completion = client.beta.chat.completions.parse(
        model=MODEL,
        messages=[
            {"role": "system", "content": """Analyze this page as if you're studying from a book. 
            
            SKIP content if the page contains:
            - Table of contents
            - Chapter listings
            - Index pages
            - Blank pages
            - Copyright information
            - Publishing details
            - References or bibliography
            - Acknowledgments
            
            DO extract knowledge if the page contains:
            - Preface content that explains important concepts
            - Actual educational content
            - Key definitions and concepts
            - Important arguments or theories
            - Examples and case studies
            - Significant findings or conclusions
            - Methodologies or frameworks
            - Critical analyses or interpretations
            
            For valid content:
            - Set has_content to true
            - Extract detailed, learnable knowledge points
            - Include important quotes or key statements
            - Capture examples with their context
            - Preserve technical terms and definitions
            
            For pages to skip:
            - Set has_content to false
            - Return empty knowledge list"""},
            {"role": "user", "content": f"Page text: {page_text}"}
        ],
        response_format=PageContent
    )
    
    result = completion.choices[0].message.parsed
    if result.has_content:
        print(colored(f"✅ Found {len(result.knowledge)} new knowledge points", "green"))
    else:
        print(colored("⏭️  Skipping page (no relevant content)", "yellow"))
    
    updated_knowledge = current_knowledge + (result.knowledge if result.has_content else [])
    
    # Update single knowledge base file
    save_knowledge_base(updated_knowledge)
    
    return updated_knowledge

def load_existing_knowledge() -> list[str]:
    knowledge_file = KNOWLEDGE_DIR / f"{Path(PDF_NAME).stem}_knowledge.json"
    if knowledge_file.exists():
        print(colored("📚 Loading existing knowledge base...", "cyan"))
        with open(knowledge_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(colored(f"✅ Loaded {len(data['knowledge'])} existing knowledge points", "green"))
            return data['knowledge']
    print(colored("🆕 Starting with fresh knowledge base", "cyan"))
    return []

def analyze_knowledge_base(client: OpenAI, knowledge_base: list[str], previous_analyses: list[str] = None) -> str:
    if not knowledge_base:
        print(colored("\n⚠️  Skipping analysis: No knowledge points collected", "yellow"))
        return ""
        
    print(colored("\n🤔 Generating analysis...", "cyan"))
    
    analysis_instructions = """Create a comprehensive and detailed summary of NEW content using markdown. Follow these guidelines carefully:

    Difficulty Rating:
    Rate each section's complexity:
    🟢 Basic - Fundamental concepts, no prior knowledge needed
    🟡 Intermediate - Builds on basic concepts
    🔴 Advanced - Requires strong understanding of prerequisites
    
    1. Book Context and Overview:
       - Start with a "Quick Take" summary (2-3 sentences)
       - Provide chapter/section number and title
       - List key themes and topics covered
       - Explain target audience and required background
       - Rate section difficulty (🟢/🟡/🔴)

    2. Key Concepts (Minimum 5):
       - Start each concept with an "In Simple Terms" explanation
       - Follow with "Deep Dive" technical details
       - Use analogies and metaphors for complex ideas
       - Show "Before → After" knowledge progression
       - Include "Common Misconceptions" warnings
       - Rate each concept's difficulty (🟢/🟡/🔴)

    3. Technical Details:
       - Begin with "ELI5" (Explain Like I'm 5) overview
       - Use progressive disclosure - simple to complex
       - Include "Quick Reference" tables
       - Provide "Step-by-Step" breakdowns
       - Add "Pro Tips" and "Gotchas"
       - Show "Real-World Examples"

    4. Visual Learning Aids:
       | Concept | Simple Terms | Technical Details | Example |
       |---------|--------------|-------------------|---------|
       | ...     | ...          | ...               | ...     |

    5. Knowledge Maps:
       ```
       [Prerequisite] → [Current Topic] → [Advanced Applications]
           ↓               ↓                ↓
       [Related-1]     [Related-2]     [Related-3]
       ```

    6. Practice and Application:
       - "Try This" exercises
       - "Think About" discussion points
       - "What If" scenarios
       - "Common Problems" and solutions
       - "Real-World Applications"

    7. Quick Reference Cards:
       ```
       📌 Quick Reference: [Topic]
       -------------------------
       Definition: 
       Key Points:
       - Point 1
       - Point 2
       Common Uses:
       - Use 1
       - Use 2
       Watch Out For:
       - Pitfall 1
       - Pitfall 2
       ```

    8. Learning Path:
       ```
       Learning Journey:
       [Beginner] → Basic Concepts (🟢)
           ↓
       [Intermediate] → Applied Knowledge (🟡)
           ↓
       [Advanced] → Expert Topics (🔴)
       ```

    9. Summary and Next Steps:
       - "5-Minute Summary" for quick review
       - "Key Takeaways" checklist
       - "Next Topics" preview
       - "Further Reading" suggestions
       - "Practice Projects" ideas

    Formatting Enhancements:
    - Use 💡 for insights
    - Use ⚠️ for warnings
    - Use 📌 for key points
    - Use 🔍 for detailed explanations
    - Use 💪 for practice exercises
    - Use 🌟 for pro tips
    - Use 🤔 for common questions
    - Use 📚 for references
    - Use 🎯 for learning objectives
    - Use ✅ for completion checklist

    Previous Summary Topics:
    {previous_topics}
    
    Remember to:
    1. Use the "Explain, Example, Exercise" pattern
    2. Include "Why This Matters" for each topic
    3. Add "In Practice" scenarios
    4. Provide "Common Mistakes" warnings
    5. Create "Quick Review" sections
    6. Use progressive complexity
    7. Link to specific page numbers
    8. Reference previous knowledge
    
    Current Content Analysis:
    """.format(
        previous_topics="\n".join([f"- {a[:100]}..." for a in previous_analyses[-CONTEXT_WINDOW:]] if previous_analyses else ["No previous analyses"])
    )
    
    completion = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system", 
                "content": analysis_instructions
            },
            {
                "role": "user", 
                "content": f"Analyze this new content batch:\n" + "\n".join(knowledge_base)
            }
        ]
    )
    
    return completion.choices[0].message.content

def setup_directories():
    # Create all necessary directories
    for directory in [PDF_DIR, KNOWLEDGE_DIR, SUMMARIES_DIR, PROGRESS_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
    
    # Clear only files related to current PDF
    current_pdf_name = Path(PDF_NAME).stem  # Use stem to get filename without extension
    if KNOWLEDGE_DIR.exists():
        for file in KNOWLEDGE_DIR.glob(f"{current_pdf_name}_knowledge.*"):
            file.unlink()
    
    if SUMMARIES_DIR.exists():
        for file in SUMMARIES_DIR.glob(f"{current_pdf_name}_*"):
            file.unlink()
    
    # Ensure PDF exists in correct location
    if not PDF_PATH.exists():
        source_pdf = Path(PDF_NAME)
        if source_pdf.exists():
            # Copy the PDF instead of moving it
            shutil.copy2(str(source_pdf), str(PDF_PATH))  # Convert paths to strings
            print(colored(f"📄 Copied PDF to analysis directory: {PDF_PATH}", "green"))
        else:
            raise FileNotFoundError(f"PDF file {PDF_NAME} not found")

def save_summary(summary: str, is_final: bool = False):
    if not summary:
        print(colored("⏭️  Skipping summary save: No content to save", "yellow"))
        return
        
    # Create markdown file with proper naming
    pdf_stem = Path(PDF_NAME).stem
    
    if is_final:
        existing_summaries = list(SUMMARIES_DIR.glob(f"{pdf_stem}_final_*.md"))
        next_number = len(existing_summaries) + 1
        summary_path = SUMMARIES_DIR / f"{pdf_stem}_final_{next_number:03d}.md"
    else:
        existing_summaries = list(SUMMARIES_DIR.glob(f"{pdf_stem}_interval_*.md"))
        next_number = len(existing_summaries) + 1
        summary_path = SUMMARIES_DIR / f"{pdf_stem}_interval_{next_number:03d}.md"
    
    # Create markdown content with metadata
    markdown_content = f"""# Book Analysis: {PDF_NAME}
Generated on: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

{summary}

---
*Analysis generated using AI Book Analysis Tool*
"""
    
    print(colored(f"\n📝 Saving {'final' if is_final else 'interval'} analysis to markdown...", "cyan"))
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    print(colored(f"✅ Analysis saved to: {summary_path}", "green"))

def parse_arguments():
    parser = argparse.ArgumentParser(description='PDF Book Analysis Tool')
    parser.add_argument('pdf_name', type=str, nargs='?', default=None,
                       help='Name of the PDF file to analyze')
    parser.add_argument('--test-pages', type=int, default=60, 
                       help='Number of pages to process in test mode. Set to 0 to process entire book')
    parser.add_argument('--interval', type=int, default=20,
                       help='Generate analysis every N pages. Set to 0 to skip interval analyses')
    return parser.parse_args()

def print_instructions():
    print(colored(f"""
📚 PDF Book Analysis Tool 📚
---------------------------
Processing file: {PDF_NAME}

Configuration:
- Test Pages: {"All pages" if TEST_PAGES is None else f"First {TEST_PAGES} pages"}
- Analysis Interval: {"Disabled" if ANALYSIS_INTERVAL is None else f"Every {ANALYSIS_INTERVAL} pages"}

Starting analysis...
""", "cyan"))

def save_progress(page_num: int, knowledge_base: list[str], previous_analyses: list[str], last_analysis_count: int):
    """Save current processing progress"""
    progress_file = PROGRESS_DIR / f"{Path(PDF_NAME).stem}_progress.pkl"
    progress = {
        'last_page': page_num,
        'knowledge_base': knowledge_base,
        'previous_analyses': previous_analyses,
        'last_analysis_count': last_analysis_count,
        'timestamp': datetime.now()
    }
    with open(progress_file, 'wb') as f:
        pickle.dump(progress, f)
    print(colored(f"💾 Progress saved at page {page_num + 1}", "blue"))

def load_progress() -> tuple[int, list[str], list[str], int]:
    """Load previous processing progress"""
    progress_file = PROGRESS_DIR / f"{Path(PDF_NAME).stem}_progress.pkl"
    if progress_file.exists():
        with open(progress_file, 'rb') as f:
            progress = pickle.load(f)
            print(colored(f"📚 Found saved progress from {progress['timestamp']}", "cyan"))
            print(colored(f"⏩ Resuming from page {progress['last_page'] + 1}", "cyan"))
            return (
                progress['last_page'],
                progress['knowledge_base'],
                progress['previous_analyses'],
                progress['last_analysis_count']
            )
    return -1, [], [], 0

def clear_progress():
    """Clear progress file after successful completion"""
    progress_file = PROGRESS_DIR / f"{Path(PDF_NAME).stem}_progress.pkl"
    if progress_file.exists():
        progress_file.unlink()

def process_pages(client: OpenAI, pdf_path, start_page: int, end_page: int, 
                 knowledge_base: list[str], previous_analyses: list[str], last_analysis_count: int) -> tuple[list[str], list[str], int]:
    """Process a range of pages with error handling and progress saving"""
    total_pages = end_page - start_page
    with open(pdf_path, 'rb') as file:
        pdf_reader = PdfReader(file)
        for page_num in range(start_page, end_page):
            try:
                # Update progress
                if progress_callback:
                    progress = (page_num - start_page + 1) / total_pages * 100
                    progress_callback(progress)
                    
                page = pdf_reader.pages[page_num]
                page_text = page.extract_text()
                
                knowledge_base = process_page(client, page_text, knowledge_base, page_num)
                
                # Generate interval analysis if ANALYSIS_INTERVAL is set
                if ANALYSIS_INTERVAL:
                    is_interval = (page_num + 1) % ANALYSIS_INTERVAL == 0
                    is_final_page = page_num + 1 == end_page
                    
                    if is_interval and not is_final_page:
                        print(colored(f"\n📊 Progress: {page_num + 1}/{end_page} pages processed", "cyan"))
                        new_knowledge = knowledge_base[last_analysis_count:]
                        interval_summary = analyze_knowledge_base(client, new_knowledge, previous_analyses)
                        previous_analyses.append(interval_summary)
                        last_analysis_count = len(knowledge_base)
                        save_summary(interval_summary, is_final=False)
                
                # Save progress after each page
                save_progress(page_num, knowledge_base, previous_analyses, last_analysis_count)
                
            except Exception as e:
                print(colored(f"\n⚠️ Error processing page {page_num + 1}: {str(e)}", "red"))
                print(colored("Progress saved. You can restart the script to continue from this point.", "yellow"))
                return knowledge_base, previous_analyses, last_analysis_count
    
    return knowledge_base, previous_analyses, last_analysis_count

if __name__ == "__main__":
    main()