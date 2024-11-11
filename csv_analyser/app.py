import os
import pandas as pd
import requests
import re
from flask import Flask, render_template, request, jsonify, flash, redirect, url_for, send_file
from werkzeug.utils import secure_filename
import google.generativeai as genai
import markdown
import io

# Initialize Flask app
app = Flask(__name__)

# Secret key for session management
app.secret_key = 'DK1329'

# Directory for uploaded files
STATIC_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'csv'}
app.config['UPLOAD_FOLDER'] = STATIC_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# API Key Configuration
with open('api.txt', 'r') as f:
    key = f.read().strip()

genai.configure(api_key=key) 
model = genai.GenerativeModel("gemini-1.5-flash")

# Ensure the upload folder exists
if not os.path.exists(STATIC_FOLDER):
    os.makedirs(STATIC_FOLDER)

# Function to check allowed file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Route for the homepage
@app.route('/')
def index():
    return render_template('index.html')

# Route to handle the upload of a CSV file and generate content
@app.route('/upload_csv', methods=['POST'])
def upload_csv():
    # Check if the file is part of the request
    if 'file' not in request.files:
        flash('No file part', 'error')
        return redirect(request.url)
    
    file = request.files['file']
    
    # Check if the file has a valid filename
    if file.filename == '':
        flash('No selected file', 'error')
        return redirect(request.url)
    
    # Check if the file type is allowed
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        try:
            # Read the CSV file using pandas
            df = pd.read_csv(file_path)

            # Pass column names to the template for dropdown population
            columns = df.columns.tolist()

            return render_template('index.html', columns=columns, filename=filename)

        except Exception as e:
            flash(f"Error processing the CSV file: {str(e)}", 'error')
            return redirect(request.url)

    flash('Invalid file type', 'error')
    return redirect(request.url)

# Route to handle the generation of content from the selected primary column
@app.route('/generate_content', methods=['POST'])
def generate_content():
    filename = request.form.get('filename')
    primary_column = request.form.get('primary_column')
    custom_prompt = request.form.get('custom_prompt')

    if not filename or not primary_column or not custom_prompt:
        flash("Please select a column and provide a prompt template", 'error')
        return redirect(request.url)

    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    try:
        # Read the CSV file again to fetch the selected primary column
        df = pd.read_csv(file_path)

        # Check if primary column exists
        if primary_column not in df.columns:
            flash(f"Primary column '{primary_column}' not found.", 'error')
            return redirect(request.url)

        # Extract entities from the primary column
        entities = df[primary_column].tolist()

        # Replace placeholder with each entity and generate content
        generated_responses = []
        for entity in entities:
            # Replace placeholder in custom prompt with the current entity
            entity_prompt = custom_prompt.format(entity=entity)
            response = generate_content_from_gemini(entity_prompt)
            
            # If response is valid, store the result
            if response and response.text:
                generated_responses.append({'entity': entity, 'response': response.text})
            else:
                generated_responses.append({'entity': entity, 'response': 'No response generated.'})

        # Render the results as a markdown file
        markdown_content = "\n".join([f"### {item['entity']}\n{item['response']}" for item in generated_responses])

        # Save the markdown content to a file in the static/uploads folder
        md_filename = 'generated_results.md'
        md_file_path = os.path.join(STATIC_FOLDER, md_filename)

        with open(md_file_path, 'w', encoding='utf-8') as md_file:
            md_file.write(markdown_content)

        # Optionally, save the CSV with the same name to serve for download
        csv_filename = f"{filename.split('.')[0]}_processed.csv"
        csv_file_path = os.path.join(STATIC_FOLDER, csv_filename)
        df.to_csv(csv_file_path, index=False)

        # Redirect to the page where the file will be displayed
        return redirect(url_for('view_md_file', filename=md_filename))

    except Exception as e:
        flash(f"Error processing the CSV file: {str(e)}", 'error')
        return redirect(request.url)

# Route to display the Markdown file content
@app.route('/view_md_file/<filename>')
def view_md_file(filename):
    md_file_path = os.path.join(STATIC_FOLDER, filename)

    if not os.path.exists(md_file_path):
        flash("Generated file not found", "error")
        return redirect(url_for('index'))

    # Read the markdown file content
    with open(md_file_path, 'r', encoding='utf-8') as md_file:
        md_content = md_file.read()

    # Convert markdown content to HTML
    html_content = markdown.markdown(md_content)

    return render_template('view_md_file.html', content=html_content)

# Function to generate content using Gemini
def generate_content_from_gemini(prompt):
    """
    Sends a request to Gemini and retrieves generated content based on the input prompt.
    """
    try:
        response = model.generate_content(prompt)
        print(response.text)  # Log the response for debugging
        return response
    except Exception as e:
        print(f"Error in generating content: {str(e)}")  # Log any errors
        return None

# Route to download the generated Markdown file
@app.route('/download_md_file/<filename>')
def download_md_file(filename):
    md_file_path = os.path.join(STATIC_FOLDER, filename)

    if not os.path.exists(md_file_path):
        flash("Generated file not found", "error")
        return redirect(url_for('index'))

    # Send the file to the user for download
    return send_file(md_file_path, as_attachment=True)

# Route to download the CSV file (if required)
@app.route('/download_csv_file/<filename>')
def download_csv_file(filename):
    csv_file_path = os.path.join(STATIC_FOLDER, filename)

    if not os.path.exists(csv_file_path):
        flash("CSV file not found", "error")
        return redirect(url_for('index'))

    # Send the CSV file to the user for download
    return send_file(csv_file_path, as_attachment=True)

# Route to preview the Google Sheet by URL
@app.route('/preview_google_sheet', methods=['POST'])
def preview_google_sheet():
    try:
        request_data = request.get_json()
        sheet_url = request_data.get('url')

        if not sheet_url:
            return jsonify({"error": "No URL provided"}), 400

        file_path = download_google_sheet_csv(sheet_url)

        if not file_path:
            return jsonify({"error": "Failed to download CSV"}), 500

        # Read and preview the CSV file
        data = pd.read_csv(file_path)
        preview_data = data.head().to_dict(orient='records')

        return jsonify(preview_data)

    except Exception as e:
        return jsonify({"error": f"Error processing Google Sheets: {str(e)}"}), 500

# Function to download Google Sheet as CSV
def download_google_sheet_csv(sheet_url):
    """
    Given the Google Sheets URL, download the sheet as a CSV file
    and save it to the static folder.
    """
    sheet_id = extract_sheet_id(sheet_url)
    if not sheet_id:
        return None

    csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    response = requests.get(csv_url)
    
    if response.status_code != 200:
        return None

    if not os.path.exists(STATIC_FOLDER):
        os.makedirs(STATIC_FOLDER)

    file_path = os.path.join(STATIC_FOLDER, f"{sheet_id}.csv")

    with open(file_path, 'wb') as file:
        file.write(response.content)

    return file_path

# Function to extract the Google Sheets ID from a URL
def extract_sheet_id(sheet_url):
    """Extracts the Google Sheets ID from a URL."""
    match = re.match(r'https://docs.google.com/spreadsheets/d/([a-zA-Z0-9-_]+)/', sheet_url)
    if match:
        return match.group(1)
    return None

# Route to handle user query (to generate content from user input)
@app.route('/handle_user_query', methods=['POST'])
def handle_user_query():
    user_query = request.form.get('query')

    if not user_query:
        flash('No query provided', 'error')
        return redirect(request.url)

    # Call the Gemini model to generate a response based on the user's query
    response = generate_content_from_gemini(user_query)

    if response and response.text:
        return jsonify({"response": response.text})
    else:
        return jsonify({"error": "No response generated"}), 400

if __name__ == '__main__':
    app.run(debug=True)
