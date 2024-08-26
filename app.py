from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
from stem import Signal, CircStatus
from stem.control import Controller
import logging
import time
from termcolor import colored
import os  

app = Flask(__name__, static_url_path='/static', template_folder='templates')

# Configuration
TOR_SOCKS_PORT = 9050
TOR_CONTROL_PORT = 9051
MAX_RETRIES = 3
RETRY_DELAY = 5
DEFAULT_RATE_LIMIT = 2
DEFAULT_PAGINATION_LIMIT = 10

# Setup logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

def connect_tor():
    session = requests.session()
    session.proxies = {
        'http': f'socks5h://localhost:{TOR_SOCKS_PORT}',
        'https': f'socks5h://localhost:{TOR_SOCKS_PORT}'
    }
    return session

def renew_tor_ip():
    try:
        with Controller.from_port(port=TOR_CONTROL_PORT) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            logging.info(colored("TOR IP address renewed.", 'cyan'))
    except Exception as e:
        logging.error(colored(f"Failed to renew TOR IP address: {e}", 'red'))

def fetch_endpoints(onion_domain, page=1):
    session = connect_tor()
    if not onion_domain.startswith('http://'):
        onion_domain = f'http://{onion_domain}'
    url = f"{onion_domain}?page={page}"
    logging.info(colored(f"Requesting URL: {url}", 'blue'))
    for attempt in range(MAX_RETRIES):
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            if 'text/html' not in response.headers.get('Content-Type', ''):
                logging.warning(colored(f"Skipping non-HTML content from {url}", 'yellow'))
                return None
            return response.text
        except requests.HTTPError as e:
            logging.error(colored(f"HTTP error fetching {url}: {e}", 'red'))
            if attempt < MAX_RETRIES - 1:
                logging.info(colored(f"Retrying in {RETRY_DELAY} seconds...", 'yellow'))
                time.sleep(RETRY_DELAY)
            else:
                return None
        except requests.RequestException as e:
            logging.error(colored(f"Request error fetching {url}: {e}", 'red'))
            if attempt < MAX_RETRIES - 1:
                logging.info(colored(f"Retrying in {RETRY_DELAY} seconds...", 'yellow'))
                time.sleep(RETRY_DELAY)
            else:
                return None

def extract_links(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    links = soup.find_all('a', href=True)
    seen_links = set()
    for link in links:
        href = link['href']
        if href.startswith('http') and href not in seen_links:
            seen_links.add(href)
            logging.info(colored(f"Endpoint found: {href}", 'green'))
    return seen_links

def save_to_file(domain, endpoints):
    """Helper function to save endpoints to a file named after the domain."""
    filename = f"{domain.replace('.onion', '').replace('http://', '').replace('/', '_')}.txt"
    filepath = os.path.join("fetched_endpoints", filename)
    
    # Create the directory if it does not exist
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    with open(filepath, 'w') as file:
        for endpoint in endpoints:
            file.write(f"{endpoint}\n")
    logging.info(colored(f"Endpoints saved to {filepath}", 'green'))

def fetch_all_endpoints(onion_domain, rate_limit, pagination_limit):
    all_endpoints = set()
    for page in range(1, pagination_limit + 1):
        logging.info(colored(f"Fetching page {page} of {onion_domain}...", 'blue'))
        html_content = fetch_endpoints(onion_domain, page)
        if html_content:
            logging.info(colored("Extracting endpoints...", 'cyan'))
            endpoints = extract_links(html_content)
            if endpoints:
                all_endpoints.update(endpoints)
            else:
                logging.info(colored("No endpoints found on this page.", 'yellow'))
        else:
            logging.error(colored("Failed to fetch content.", 'red'))
            renew_tor_ip()
        time.sleep(rate_limit)

    # Save the fetched endpoints to a file
    save_to_file(onion_domain, all_endpoints)
    
    return all_endpoints

def get_last_entry_exit_relay():
    entry_node = {}
    exit_node = {}
    try:
        with Controller.from_port(port=TOR_CONTROL_PORT) as controller:
            controller.authenticate()
            controller.signal(Signal.NEWNYM)
            last_circuit = None
            for circ in controller.get_circuits():
                if circ.status != CircStatus.BUILT:
                    continue
                last_circuit = circ
            if last_circuit:
                entry_fp, entry_nickname = last_circuit.path[0]
                entry_desc = controller.get_network_status(entry_fp, None)
                entry_address = entry_desc.address if entry_desc else 'unknown'
                entry_node = {
                    "fingerprint": entry_fp,
                    "nickname": entry_nickname,
                    "address": entry_address
                }
                exit_fp, exit_nickname = last_circuit.path[-1]
                exit_desc = controller.get_network_status(exit_fp, None)
                exit_address = exit_desc.address if exit_desc else 'unknown'
                exit_node = {
                    "fingerprint": exit_fp,
                    "nickname": exit_nickname,
                    "address": exit_address
                }
                if entry_fp == exit_fp:
                    controller.signal(Signal.NEWNYM)
    except Exception as e:
        logging.error(colored(f"An error occurred: {e}", 'red'))
    return entry_node, exit_node

@app.route('/')
def index():
    try:
        entry_node, exit_node = get_last_entry_exit_relay()
        return render_template("index.html", entry_node=entry_node, exit_node=exit_node)
    except Exception as e:
        logging.error(colored(f"Error in index route: {e}", 'red'))
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/fetch_endpoints', methods=['POST'])
def fetch_endpoints_route():
    try:
        data = request.form
        domain = data.get('domain')
        rate_limit = int(data.get('rate_limit', DEFAULT_RATE_LIMIT))
        pagination_limit = min(int(data.get('pagination_limit', DEFAULT_PAGINATION_LIMIT)), DEFAULT_PAGINATION_LIMIT)
        
        # Enforce maximum pagination limit
        if pagination_limit > 10:
            pagination_limit = 10

        if not domain or not domain.endswith('.onion'):
            return jsonify({'error': "Invalid domain"}), 400

        all_endpoints = fetch_all_endpoints(domain, rate_limit, pagination_limit)
        
        if all_endpoints:
            return jsonify({'endpoints': list(all_endpoints)})
        else:
            return jsonify({'error': 'No endpoints found'}), 404
    except Exception as e:
        logging.error(colored(f"Error in fetch_endpoints_route: {e}", 'red'))
        return jsonify({'error': 'Internal server error'}), 500
        

if __name__ == "__main__":
    app.run(debug=True)
