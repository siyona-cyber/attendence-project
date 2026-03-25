import math
import os
import sqlite3
from datetime import datetime

from flask import Flask, jsonify, render_template, request

try:
    import psycopg2
except ImportError:
    psycopg2 = None


app = Flask(__name__, template_folder="template")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
SQLITE_PATH = os.getenv("DB_PATH", "attendance.db")
IS_POSTGRES = DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")


def get_postgres_dsn():
    if DATABASE_URL.startswith("postgres://"):
        return DATABASE_URL.replace("postgres://", "postgresql://", 1)
    return DATABASE_URL


def get_db_connection():
    if IS_POSTGRES:
        if psycopg2 is None:
            raise RuntimeError("psycopg2 is not installed. Install requirements to use PostgreSQL.")
        return psycopg2.connect(get_postgres_dsn())
    return sqlite3.connect(SQLITE_PATH)


def init_feedback_table():
    with get_db_connection() as conn:
        if IS_POSTGRES:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS feedback (
                        id SERIAL PRIMARY KEY,
                        name TEXT NOT NULL,
                        email TEXT,
                        rating INTEGER NOT NULL,
                        message TEXT NOT NULL,
                        created_at TIMESTAMP NOT NULL
                    )
                    """
                )
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT,
                    rating INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
        conn.commit()


def save_feedback(name, email, rating, message):
    with get_db_connection() as conn:
        query = """
            INSERT INTO feedback (name, email, rating, message, created_at)
            VALUES (%s, %s, %s, %s, %s)
        """ if IS_POSTGRES else """
            INSERT INTO feedback (name, email, rating, message, created_at)
            VALUES (?, ?, ?, ?, ?)
        """
        created_at = datetime.utcnow()
        stored_time = created_at if IS_POSTGRES else created_at.isoformat(timespec="seconds")

        if IS_POSTGRES:
            with conn.cursor() as cursor:
                cursor.execute(query, (name, email or None, rating, message, stored_time))
        else:
            conn.execute(query, (name, email or None, rating, message, stored_time))
        conn.commit()


DB_INIT_ERROR = None
try:
    init_feedback_table()
except Exception as exc:
    DB_INIT_ERROR = str(exc)


@app.get("/health")
def health():
    try:
        with get_db_connection() as conn:
            if IS_POSTGRES:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    row = cursor.fetchone()
                    db_ok = bool(row and row[0] == 1)
            else:
                row = conn.execute("SELECT 1").fetchone()
                db_ok = bool(row and row[0] == 1)

        payload = {
            "status": "ok" if db_ok else "degraded",
            "database": "ok" if db_ok else "unhealthy",
            "db_backend": "postgresql" if IS_POSTGRES else "sqlite",
        }
        if DB_INIT_ERROR:
            payload["startup_warning"] = DB_INIT_ERROR

        return jsonify(payload), 200 if db_ok else 503
    except Exception as exc:
        return jsonify(
            {
                "status": "unhealthy",
                "database": "unreachable",
                "db_backend": "postgresql" if IS_POSTGRES else "sqlite",
                "error": str(exc),
            }
        ), 503


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    error = None
    feedback_error = None
    feedback_success = None

    inputs = {
        "total_classes": "",
        "attended_classes": "",
        "target_percentage": "75",
    }

    feedback_inputs = {
        "name": "",
        "email": "",
        "rating": "5",
        "message": "",
    }

    if request.method == "POST":
        form_type = request.form.get("form_type", "attendance")

        if form_type == "attendance":
            inputs["total_classes"] = request.form.get("total_classes", "").strip()
            inputs["attended_classes"] = request.form.get("attended_classes", "").strip()
            inputs["target_percentage"] = request.form.get("target_percentage", "75").strip()

            try:
                total_classes = int(inputs["total_classes"])
                attended_classes = int(inputs["attended_classes"])
                target_percentage = float(inputs["target_percentage"])

                if total_classes <= 0:
                    raise ValueError("Total classes must be greater than 0.")
                if attended_classes < 0:
                    raise ValueError("Attended classes cannot be negative.")
                if attended_classes > total_classes:
                    raise ValueError("Attended classes cannot be more than total classes.")
                if not (1 <= target_percentage < 100):
                    raise ValueError("Target percentage must be between 1 and 99.99.")

                absent_classes = total_classes - attended_classes
                current_percentage = round((attended_classes / total_classes) * 100, 2)
                target_ratio = target_percentage / 100

                # If you attend every future class, these many classes are needed to reach target.
                if current_percentage >= target_percentage:
                    classes_needed = 0
                else:
                    classes_needed = math.ceil(
                        ((target_ratio * total_classes) - attended_classes) / (1 - target_ratio)
                    )

                # Max additional classes you can miss while staying at or above target.
                max_missable = math.floor((attended_classes / target_ratio) - total_classes)
                max_missable = max(max_missable, 0)

                result = {
                    "current_percentage": current_percentage,
                    "absent_classes": absent_classes,
                    "classes_needed": classes_needed,
                    "max_missable": max_missable,
                    "is_safe": current_percentage >= target_percentage,
                    "target_percentage": round(target_percentage, 2),
                }
            except ValueError as exc:
                error = str(exc)

        elif form_type == "feedback":
            feedback_inputs["name"] = request.form.get("name", "").strip()
            feedback_inputs["email"] = request.form.get("email", "").strip()
            feedback_inputs["rating"] = request.form.get("rating", "5").strip()
            feedback_inputs["message"] = request.form.get("message", "").strip()

            try:
                name = feedback_inputs["name"]
                email = feedback_inputs["email"]
                message = feedback_inputs["message"]
                rating = int(feedback_inputs["rating"])

                if not name:
                    raise ValueError("Please enter your name.")
                if not message:
                    raise ValueError("Please add your feedback message.")
                if not (1 <= rating <= 5):
                    raise ValueError("Rating must be between 1 and 5.")

                save_feedback(name=name, email=email, rating=rating, message=message)
                feedback_success = "Thanks for your feedback. It has been submitted successfully."
                feedback_inputs = {
                    "name": "",
                    "email": "",
                    "rating": "5",
                    "message": "",
                }
            except ValueError as exc:
                feedback_error = str(exc)

    return render_template(
        "index.html",
        result=result,
        error=error,
        inputs=inputs,
        feedback_inputs=feedback_inputs,
        feedback_error=feedback_error,
        feedback_success=feedback_success,
    )


if __name__ == "__main__":
    app.run(debug=True)
