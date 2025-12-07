import yaml
import feedparser
import trafilatura
import requests
import time
import os
import datetime
import re
import glob
from urllib.parse import quote, urlparse

# --- Configuration & Setup ---

FEEDS_DIR = 'feeds'
ARTICLES_DIR = 'articles'

def load_configs():
    configs = []
    feed_files = glob.glob(os.path.join(FEEDS_DIR, '*.yaml'))
    for file_path in feed_files:
        print(f"Loading config: {file_path}")
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
            if data and 'feeds' in data:
                configs.extend(data['feeds'])
    return configs

def sanitize_filename(name):
    return re.sub(r'[\\/*?:\"<>|]', "", name).strip().replace(" ", "_")[:100]

def determine_category(feed_url):
    """
    Parses the Medium RSS URL to determine the sub-directory name.
    Examples:
    - https://medium.com/feed/@username -> @username
    - https://medium.com/feed/publication-name -> publication-name
    - https://medium.com/feed/tag/tag-name -> tag_tag-name
    """
    try:
        parsed = urlparse(feed_url)
        path = parsed.path # e.g., /feed/tag/prompt-engineering
        
        if '/feed/' not in path:
            return "unknown"
            
        suffix = path.split('/feed/')[-1]
        
        # Handle tags specifically to avoid subdirectories
        if suffix.startswith('tag/'):
            return suffix.replace('/', '_')
            
        # Clean up any trailing slashes
        return suffix.strip('/')
    except Exception:
        return "unknown"

# --- Fetching Strategies ---

def fetch_direct(url):
    try:
        downloaded = trafilatura.fetch_url(url)
        text = trafilatura.extract(downloaded) if downloaded else None
        if text and len(text) > 300:
            return text, "Direct"
    except Exception as e:
        print(f"  Direct error: {e}")
    return None, None

def fetch_jina(url):
    try:
        jina_url = f"https://r.jina.ai/{url}"
        r = requests.get(jina_url, timeout=20)
        if r.status_code == 200 and len(r.text) > 300:
            if "Access denied" not in r.text[:500]:
                return r.text, "Jina Reader"
    except Exception as e:
        print(f"  Jina error: {e}")
    return None, None

def fetch_wayback(url):
    try:
        api_url = f"https://archive.org/wayback/available?url={url}"
        r = requests.get(api_url, timeout=10)
        data = r.json()
        if 'archived_snapshots' in data and 'closest' in data['archived_snapshots']:
            wb_url = data['archived_snapshots']['closest']['url']
            print(f"  Found Wayback snapshot: {wb_url}")
            downloaded = trafilatura.fetch_url(wb_url)
            text = trafilatura.extract(downloaded) if downloaded else None
            if text and len(text) > 300:
                return text, "Wayback Machine"
    except Exception as e:
        print(f"  Wayback error: {e}")
    return None, None

def fetch_google_cache(url):
    try:
        cache_url = f"http://webcache.googleusercontent.com/search?q=cache:{quote(url)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(cache_url, headers=headers, timeout=10)
        if r.status_code == 200:
            if "That’s an error." in r.text and "The requested URL was not found" in r.text:
                return None, None
            text = trafilatura.extract(r.text)
            if text and len(text) > 300:
                return text, "Google Cache"
    except Exception as e:
        print(f"  Google Cache error: {e}")
    return None, None

def fetch_content_with_fallbacks(url):
    print(f"Fetching: {url}")
    strategies = [fetch_direct, fetch_jina, fetch_google_cache, fetch_wayback]
    
    for strategy in strategies:
        strategy_name = strategy.__name__.replace("fetch_", "").replace("_", " ").title()
        print(f"  Trying: {strategy_name}...")
        content, source = strategy(url)
        if content:
            print(f"  Success via {source}")
            return content, source
    return None, None

# --- Main Logic ---

def main():
    feeds = load_configs()
    if not feeds:
        print("No feeds found in configuration.")
        return

    today_str = datetime.date.today().isoformat()
    
    for feed_url in feeds:
        print(f"\nProcessing Feed: {feed_url}")
        category = determine_category(feed_url)
        
        # Directory structure: articles/YYYY-MM-DD/Category/
        output_dir = os.path.join(ARTICLES_DIR, today_str, sanitize_filename(category))
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        d = feedparser.parse(feed_url)
        
        if not d.entries:
            print("  No entries found.")
            continue

        # Take top 3
        entries = d.entries[:3]
        
        for i, entry in enumerate(entries):
            title = entry.title
            link = entry.link
            
            # Check if file already exists to avoid re-fetching
            filename = f"{sanitize_filename(title)}.md"
            filepath = os.path.join(output_dir, filename)
            
            if os.path.exists(filepath):
                print(f"  Skipping (already exists): {title}")
                continue

            print(f"\n  --- Article {i+1}: {title} ---")
            
            content, source = fetch_content_with_fallbacks(link)
            
            if content:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(f"# {title}\n\n")
                    f.write(f"**Source URL**: {link}\n")
                    f.write(f"**Feed**: {feed_url}\n")
                    f.write(f"**Category**: {category}\n")
                    f.write(f"**Fetch Source**: {source}\n")
                    f.write(f"**Date**: {datetime.datetime.now().isoformat()}\n\n")
                    f.write("---\n\n")
                    f.write(content)
                print(f"  Saved to: {filepath}")
            else:
                print("  Failed to retrieve content.")
            
            time.sleep(1)

if __name__ == "__main__":
    main()
