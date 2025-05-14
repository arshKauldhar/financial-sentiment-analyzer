from flask import Flask, render_template, request, redirect, url_for,flash, session
import psycopg2
import pickle
from dotenv import load_dotenv
import os
from datetime import date
from transformers import TextClassificationPipeline
from werkzeug.security import generate_password_hash, check_password_hash

# Load the saved model and tokenizer
with open("models\sentiment_model_pipeline.pkl", "rb") as f:
    model, tokenizer = pickle.load(f)

# Create a sentiment analysis pipeline with top_k=None
pipeline = TextClassificationPipeline(model=model, tokenizer=tokenizer, top_k=None)


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

def get_db_connection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST")
    )



label_map = {
    'negative': 'Negative',
    'neutral': 'Neutral',
    'positive': 'Positive'
}


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']

        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT id, password_hash FROM users WHERE username = %s", (username,))
            user = cur.fetchone()
            cur.close()
            conn.close()

            if user and check_password_hash(user[1], password):
                session['user_id'] = user[0]  # Store user ID in session
                session['username'] = username
                return redirect(url_for('home'))
            else:
                flash('Invalid username or password.', 'danger')

        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username'].strip()
        email = request.form['email'].strip()
        password = request.form['password']

        password_hash = generate_password_hash(password)

        try:
            conn = get_db_connection()
            cur = conn.cursor()

            # Check if email already exists
            cur.execute("SELECT * FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                flash('The email is already registered. Please use a different one.', 'danger')
                cur.close()
                conn.close()
                return render_template('register.html')

            # Check if username already exists
            cur.execute("SELECT * FROM users WHERE username = %s", (username,))
            if cur.fetchone():
                flash('The username is already taken. Please choose another.', 'danger')
                cur.close()
                conn.close()
                return render_template('register.html')

            # Insert the new user
            cur.execute("""
                INSERT INTO users (username, email, password_hash)
                VALUES (%s, %s, %s)
            """, (username, email, password_hash))

            conn.commit()
            cur.close()
            conn.close()

            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))

        except Exception as e:
            conn.rollback()
            flash('An unexpected error occurred. Please try again.', 'danger')
            # Optionally log the error for debugging
            print(f'Registration error: {e}')
            if cur:
                cur.close()
            if conn:
                conn.close()

    return render_template('register.html')

@app.route('/home', methods=['GET', 'POST'])
def home():
    sentiment = None

    if request.method == 'POST':
        news_text = request.form['news_text']
        prediction = pipeline(news_text)
        most_accurate = max(prediction[0], key=lambda x: x['score'])
        sentiment = label_map.get(most_accurate['label'].lower(), most_accurate['label'])
        most_accurate_score = round(most_accurate['score'], 4)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO news_sentiment (user_id, content, sentiment) VALUES (%s, %s, %s)",
            (session['user_id'], news_text, sentiment)
        )
        conn.commit()
        cur.close()
        conn.close()

    return render_template('home.html', sentiment=sentiment)

@app.route('/history', methods=['GET'])
def view_history():
    if 'user_id' not in session:
        flash("You must be logged in to view your history.", "danger")
        return redirect(url_for('login'))

    user_id = session['user_id']

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    # Start building the query
    query = "SELECT content, sentiment, created_at FROM news_sentiment WHERE user_id = %s"
    params = [user_id]

    # Add filters based on the input
    if start_date:
        query += " AND created_at >= %s"
        params.append(start_date + " 00:00:00")  # Ensure full date-time format
    if end_date:
        query += " AND created_at <= %s"
        params.append(end_date + " 23:59:59")

    query += " ORDER BY created_at DESC"

    # Execute the query
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    history_items = [{
        'date': row[2].strftime('%Y-%m-%d'),
        'sentiment': row[1],
        'summary': row[0][:100] + "..." if len(row[0]) > 100 else row[0]
    } for row in rows]

    return render_template('history.html', history_items=history_items, start_date=start_date, end_date=end_date)

@app.route('/submit_feedback', methods=['POST'])
def submit_feedback():
    # Get the rating and comment from the form
    rating = request.form.get('rating')
    comment = request.form.get('feedback')

    # Ensure the user is logged in (check if user_id exists in session)
    if 'user_id' not in session:
        flash("You must be logged in to submit feedback.", "danger")
        return redirect(url_for('login'))  # Redirect to login page if not logged in

    user_id = session['user_id']  # Get the current user's ID from the session

    # Connect to the database
    conn = get_db_connection()
    cur = conn.cursor()

    # Insert the feedback into the feedback table
    cur.execute(
        "INSERT INTO feedback (user_id, rating, comment) VALUES (%s, %s, %s)",
        (user_id, rating, comment)
    )
    conn.commit()  # Commit the transaction
    cur.close()
    conn.close()

    flash("Thank you for your feedback!", "success")  # Flash a success message
    return redirect(url_for('home'))  # Redirect back to the home page

@app.route('/logout')
def logout():
    session.pop('username', None)  # Remove username from session
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    # Ensure user is logged in
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']

        conn = get_db_connection()
        cur = conn.cursor()

        # Check if the current password matches
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (session['user_id'],))
        user = cur.fetchone()

        if not user or not check_password_hash(user[0], current_password):
            flash('Incorrect current password.', 'danger')
        elif new_password != confirm_password:
            flash('New passwords do not match.', 'danger')
        else:
            # Update the password if valid
            hashed_pw = generate_password_hash(new_password)
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hashed_pw, session['user_id']))
            conn.commit()
            flash('Password changed successfully.', 'success')

        cur.close()
        conn.close()

        # Redirect to home after successful password change
        return redirect(url_for('home'))

    # Render the change password page if it's a GET request
    return render_template('change_password.html')

@app.route('/delete_account', methods=['POST', 'GET'])
def delete_account():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("DELETE FROM users WHERE id = %s", (session['user_id'],))
        conn.commit()

        cur.close()
        conn.close()

        session.clear()
        flash('Your account has been deleted.', 'info')
        return redirect(url_for('login'))

    return render_template('delete_account.html')

if __name__ == "__main__":
    app.run(debug=True)