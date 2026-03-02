from functools import wraps
import json
import os
import uuid

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user
from werkzeug.utils import secure_filename
from config import Config
from models import USER_ROLES, Report, User, db

app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message_category = "error"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles:
                flash("You do not have permission to access this page.", "error")
                return render_template(
                    "forbidden.html",
                    allowed_roles=[role.capitalize() for role in roles],
                    current_role=current_user.role.capitalize()
                ), 403
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


def parse_coordinates(location_value):
    if not location_value:
        return None, None
    try:
        lat_str, lng_str = [value.strip() for value in location_value.split(",", 1)]
        return float(lat_str), float(lng_str)
    except (ValueError, AttributeError):
        return None, None


def parse_report_details(report):
    details = {
        "animal_type": "Animal",
        "urgency": "Medium",
        "contact_number": "",
        "report_note": "Injury report"
    }
    if report.description:
        try:
            saved = json.loads(report.description)
            details.update(saved)
        except (json.JSONDecodeError, TypeError):
            details["report_note"] = report.description
    return details

@app.route('/')
def home():
    return render_template("index.html")


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == 'POST':
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "citizen").strip().lower()

        if not name or not email or not password:
            flash("Name, email, and password are required.", "error")
            return render_template("register.html", roles=USER_ROLES)

        if role not in USER_ROLES:
            flash("Invalid role selected.", "error")
            return render_template("register.html", roles=USER_ROLES)

        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return render_template("register.html", roles=USER_ROLES)

        user = User(name=name, email=email, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash("Account created successfully. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", roles=USER_ROLES)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == 'POST':
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Invalid email or password.", "error")
            return render_template("login.html")

        login_user(user)
        flash("Logged in successfully.", "success")
        next_url = request.args.get("next")
        return redirect(next_url or url_for("dashboard"))

    return render_template("login.html")


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


@app.route('/dashboard')
@login_required
def dashboard():
    return render_template("dashboard.html")

@app.route('/report')
@role_required('citizen', 'admin')
def report():
    return render_template("report.html")


@app.route('/api/reports', methods=['POST'])
@role_required('citizen', 'admin')
def submit_report_api():
    animal_type = request.form.get("animalType", "").strip() or "Animal"
    contact_number = request.form.get("contactNumber", "").strip()
    urgency = request.form.get("urgency", "Medium").strip().title()
    coordinates = request.form.get("coordinates", "").strip()
    report_note = request.form.get("note", "Injury report").strip() or "Injury report"

    image_file = request.files.get("image")
    if not image_file:
        return jsonify({"success": False, "message": "Image is required."}), 400

    latitude, longitude = parse_coordinates(coordinates)
    if latitude is None or longitude is None:
        return jsonify({"success": False, "message": "Valid coordinates are required."}), 400

    filename = secure_filename(image_file.filename or "animal.jpg")
    unique_name = f"{uuid.uuid4().hex}_{filename}"
    upload_folder = os.path.join(app.root_path, "static", "uploads")
    os.makedirs(upload_folder, exist_ok=True)
    saved_path = os.path.join(upload_folder, unique_name)
    image_file.save(saved_path)

    urgency_to_status = {
        "High": "Emergency",
        "Medium": "In Progress",
        "Low": "In Progress"
    }
    report_status = urgency_to_status.get(urgency, "In Progress")

    description_payload = {
        "animal_type": animal_type,
        "urgency": urgency,
        "contact_number": contact_number,
        "report_note": report_note
    }

    report = Report(
        description=json.dumps(description_payload),
        location=f"{latitude:.6f}, {longitude:.6f}",
        image_path=f"/static/uploads/{unique_name}",
        status=report_status,
        user_id=current_user.id
    )
    db.session.add(report)
    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Emergency report submitted successfully.",
        "reportId": report.id
    }), 201


@app.route('/api/reports/active', methods=['GET'])
@role_required('volunteer', 'ngo', 'admin')
def active_reports_api():
    reports = (
        Report.query
        .filter(Report.status.in_(["Emergency", "In Progress", "Pending"]))
        .order_by(Report.id.desc())
        .all()
    )

    payload = []
    for report in reports:
        reporter_lat, reporter_lng = parse_coordinates(report.location)
        if reporter_lat is None or reporter_lng is None:
            continue

        details = parse_report_details(report)
        animal_type = details.get("animal_type") or "Animal"
        urgency = details.get("urgency") or "Medium"
        contact_number = details.get("contact_number") or ""

        payload.append({
            "id": report.id,
            "label": f"{animal_type} - Injury Report",
            "status": report.status,
            "urgency": urgency,
            "contactNumber": contact_number,
            "reporterLat": reporter_lat,
            "reporterLng": reporter_lng,
            "lat": reporter_lat,
            "lng": reporter_lng,
            "imagePath": report.image_path,
            "note": details.get("report_note", "")
        })

    return jsonify({"success": True, "reports": payload})


@app.route('/api/stats', methods=['GET'])
def live_stats_api():
    rescued_animals = Report.query.filter(Report.status.in_(["Done", "Rescued"])) .count()
    volunteer_count = User.query.filter_by(role="volunteer").count()
    ngo_count = User.query.filter_by(role="ngo").count()

    return jsonify({
        "success": True,
        "stats": {
            "animalsRescued": rescued_animals,
            "activeVolunteers": volunteer_count,
            "partnerNgos": ngo_count
        }
    })

@app.route('/volunteer')
@role_required('volunteer', 'admin')
def volunteer():
    return render_template("volunteer.html")


@app.route('/ngo')
@role_required('ngo', 'admin')
def ngo():
    return render_template("ngo.html")


@app.route('/admin')
@role_required('admin')
def admin():
    return render_template("admin.html")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run()
