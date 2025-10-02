from flask import Flask, request, jsonify
from flask_cors import CORS 
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt # For password hashing
from pathlib import Path 
import onnxruntime as ort
import numpy as np
import os
import joblib
import json # To convert forecast_data to/from JSON string
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv 
import logging
from sqlalchemy import Text # Import Text type

# Configure basic logging for visibility
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration & Initialization ---

# Define the base directory (where app.py resides)
BASE_DIR = Path(__file__).resolve().parent

# Explicitly load environment variables from the .env file in the same directory
load_dotenv(BASE_DIR / '.env') 

app = Flask(__name__)
CORS(app) 

# Configure Database - Use SQLite for development if no DATABASE_URL is set
database_url = os.environ.get('DATABASE_URL')
if not database_url:
    # Use SQLite as fallback for development
    database_url = 'sqlite:///demand_forecast.db'
    
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'FALLBACK_NEVER_USE_THIS_IN_PROD') 

db = SQLAlchemy(app)
bcrypt = Bcrypt(app) 
CITY_TO_STATE_MAP = {
    "Lucknow": "Uttar Pradesh", 
    "Kanpur": "Uttar Pradesh", 
    "Meerut": "Uttar Pradesh", 
    "Agra": "Uttar Pradesh", 
    "Varanasi": "Uttar Pradesh",
    "Mumbai": "Maharashtra", 
    "Pune": "Maharashtra", 
    "Nagpur": "Maharashtra",
    "Bengaluru": "Karnataka", 
    "Mysore": "Karnataka",
    "Chennai": "Tamil Nadu", 
    "Coimbatore": "Tamil Nadu",
    "Kolkata": "West Bengal", 
    "Siliguri": "West Bengal",
    "Jaipur": "Rajasthan", 
    "Jodhpur": "Rajasthan",
    "Ahmedabad": "Gujarat", 
    "Surat": "Gujarat",
    "Hyderabad": "Telangana", 
    "Warangal": "Telangana",
    "Delhi": "Delhi",
}

def get_state_from_city(city):
    """Retrieves the state name from the city name."""
    return CITY_TO_STATE_MAP.get(city, "Unknown")
# --- Database Models (Mapped to PostgreSQL Tables) ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'employee' or 'admin'
    state = db.Column(db.String(50), nullable=True) # Admin's state oversight
    admin_level = db.Column(db.String(20), nullable=True) # 'state' or 'central' for admins, None for employees
    

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # INCREASED LENGTHS to prevent data truncation errors during database save
    budget = db.Column(db.String(100), nullable=False) # Increased from 50
    location = db.Column(db.String(100), nullable=False) # Increased from 50
    tower_type = db.Column(db.String(100), nullable=False) # Increased from 50
    substation_type = db.Column(db.String(255), nullable=False) # Increased from 100 (HIGH RISK field)
    geo = db.Column(db.String(100), nullable=False) # Increased from 50
    taxes = db.Column(db.String(100), nullable=False) # Increased from 50
    
    status = db.Column(db.String(50), default='pending', nullable=False)  # Increased to handle "pending central approval" 
    created_at = db.Column(db.String(50), nullable=False) 
    
    created_by_email = db.Column(db.String(120), db.ForeignKey('user.email'), nullable=False)
    
    forecast_data = db.Column(db.Text, nullable=True) 

    def to_dict(self):
        # Parses the stored JSON string back into a Python dictionary for the frontend
        return {
            'id': self.id,
            'budget': self.budget,
            'location': self.location,
            'towerType': self.tower_type,
            'substationType': self.substation_type,
            'geo': self.geo,
            'taxes': self.taxes,
            'status': self.status,
            'createdAt': self.created_at,
            'createdBy': self.created_by_email,
            # The database stores budget as a string, but the frontend handles the display.
            'allForecasts': json.loads(self.forecast_data) if self.forecast_data else {}
        }


# --- Model Loading ---

MODEL_ASSET_MAPPING = {
    "steel": { "onnx": "steel_model.onnx", "scaler": "scaler_steel.joblib", "columns": "model_columns_steel.joblib", },
    "conductor": { "onnx": "conductor_model.onnx", "scaler": "scaler_conductor.joblib", "columns": "model_columns_conductor.joblib", },
    "transformers": { "onnx": "transformers_model.onnx", "scaler": "scaler_transformers.joblib", "columns": "model_columns_transformers.joblib", },
    "earthwire": { "onnx": "earthwire_model.onnx", "scaler": "scaler_earthwire.joblib", "columns": "model_columns_earthwire.joblib", },
    "foundation": { "onnx": "foundation_model.onnx", "scaler": "scaler_foundation.joblib", "columns": "model_columns_foundation.joblib", },
    "reactors": { "onnx": "reactors_model.onnx", "scaler": "scaler_reactors.joblib", "columns": "model_columns_reactors.joblib", },
    "tower": { "onnx": "tower_model.onnx", "scaler": "scaler_tower.joblib", "columns": "model_columns_tower.joblib", },
}

LOADED_MODELS = {}

logging.info("--- Starting Model Asset Loading ---")
for model_name, paths in MODEL_ASSET_MAPPING.items():
    try:
        # Check if assets exist before loading
        if not all(Path(p).exists() for p in paths.values()):
             raise FileNotFoundError(f"Missing one or more model files in the current directory.")

        session = ort.InferenceSession(paths["onnx"]) 
        scaler = joblib.load(paths["scaler"])
        columns = joblib.load(paths["columns"])
        
        input_name = session.get_inputs()[0].name
        output_name = session.get_outputs()[0].name
        
        LOADED_MODELS[model_name] = {
            "session": session, "scaler": scaler, "columns": columns, "input_name": input_name, "output_name": output_name,
        }
        logging.info(f"✅ Successfully loaded assets for: {model_name.upper()}")
    except Exception as e:
        logging.error(f"❌ Error loading assets for {model_name.upper()}. Details: {e}")

if not LOADED_MODELS:
    logging.critical("FATAL: No models were loaded successfully. Predictions will fail.")


# --- Feature Mapping and Engineering Function (Enhanced Error Handling) ---

def create_feature_vector(input_data, columns):
    """
    Transforms raw input data into a feature vector matching the model's expected columns.
    
    Args:
        input_data (dict): Raw input features from the frontend.
        columns (list): List of column names (features) expected by the ML model.
        
    Returns:
        list: An ordered list of float features ready for scaling and prediction.
        
        Raises:
            ValueError: If a critical field is missing or malformed during conversion.
    """
    feature_vector = {col: 0.0 for col in columns}
    logging.debug(f"Target columns size: {len(columns)}")

    try:
        # --- NUMERIC FEATURES ---
        
        # 1. TOWER TYPE (Voltage)
        if "towerType" in input_data and input_data["towerType"]:
            # Example: "400 kV" -> 400.0
            tower_kv_str = str(input_data["towerType"]).split(' ')[0]
            tower_kv = float(tower_kv_str)
            if 'Voltage_kV' in feature_vector: 
                feature_vector['Voltage_kV'] = tower_kv
            logging.debug(f"Processed towerType to Voltage_kV: {tower_kv}")
        else:
            raise ValueError("Missing or invalid input for 'towerType'.")
        
        # 2. BUDGET (Estimated Cost)
        if "budget" in input_data and input_data["budget"] is not None:
            # We must handle the case where the budget comes as a string (if frontend wasn't updated) 
            # or already as a number (if frontend was updated).
            budget_val = float(input_data["budget"])
            if 'Estimated_Cost_Million' in feature_vector: 
                # Convert to Millions (assuming input is raw INR)
                feature_vector['Estimated_Cost_Million'] = budget_val / 1000000.0
            logging.debug(f"Processed budget to Estimated_Cost_Million: {budget_val / 1000000.0}")
        else:
            raise ValueError("Missing or invalid input for 'budget'.")

    except (ValueError, TypeError, IndexError) as e:
        logging.error(f"Feature processing error in numeric fields: {e}")
        # Re-raise the error to be caught by the API route handler
        raise ValueError(f"Required numeric data malformed (Budget/Tower Type): {e}")
        
    # --- CATEGORICAL (ONE-HOT) FEATURES ---
    
    # 3. LOCATION
    if "location" in input_data:
        location_key = f"Location_ {input_data['location']}"
        if location_key in feature_vector: 
            feature_vector[location_key] = 1.0
        logging.debug(f"Set location key: {location_key}")

    # 4. SUBSTATION TYPE
    if "substationType" in input_data:
        # Apply the complex formatting to match ML model column names
        substation_raw = input_data['substationType']
        substation_key = f"Substation_Type_ {substation_raw}"
        # THIS LINE IS EXTREMELY FRAGILE: Ensure it perfectly matches your column names
        # Fixed: Simplified replacement logic to minimize risk of mismatch
        substation_key = substation_key.replace(" (", "_(").replace(" ", "_").replace("(", "_").replace(")", "") 
        
        if substation_key in feature_vector: 
            feature_vector[substation_key] = 1.0
        else:
            # CRITICAL LOGGING: This indicates a mismatch between UI dropdown and ML model columns.
            logging.warning(f"Substation key '{substation_key}' NOT found in model columns.")
            
        logging.debug(f"Set substation key: {substation_key}")


    # 5. GEOGRAPHICAL ZONE
    if "geo" in input_data:
        geo_key = f"Geographical_Zone_ {input_data['geo']}"
        if geo_key in feature_vector: 
            feature_vector[geo_key] = 1.0
        logging.debug(f"Set geo key: {geo_key}")


    # 6. TAXES APPLICABLE
    if "taxes" in input_data:
        taxes_key = f"Taxes_Applicable_{input_data['taxes']}"
        if taxes_key in feature_vector: 
            feature_vector[taxes_key] = 1.0
        logging.debug(f"Set taxes key: {taxes_key}")

    # Return the feature vector values in the order defined by columns (MANDATORY)
    return [feature_vector[col] for col in columns]


# --- API Routes: Authentication (No Changes) ---

@app.route('/api/auth/signup', methods=['POST'])
def signup():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    role = data.get('role')
    
    # Validation
    if not all([email, password, role]):
        return jsonify({"message": "Missing required fields"}), 400
        
    if role == 'admin' and not data.get('admin_level'):
        return jsonify({"message": "Admin level is required for admin users"}), 400
        
    if role == 'admin' and not data.get('state'):
        return jsonify({"message": "State is required for admin users"}), 400
    
    if User.query.filter_by(email=email).first():
        return jsonify({"message": "User already exists"}), 409

    hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
    
    # Only set admin_level for admin users
    admin_level = data.get('admin_level') if role == 'admin' else None
    
    new_user = User(
        name=data.get('name'), 
        email=email, 
        password_hash=hashed_password,
        role=role,
        state=data.get('state'),
        admin_level=admin_level
    )
    
    try:
        db.session.add(new_user)
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        logging.error(f"Database error during signup: {e}")
        return jsonify({"message": "Database error during signup"}), 500
    
    return jsonify({
        "message": "User created successfully",
        "user": { 
            "email": new_user.email, 
            "name": new_user.name, 
            "role": new_user.role, 
            "state": new_user.state, 
            "admin_level": new_user.admin_level 
        }
    }), 201

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')
    
    user = User.query.filter_by(email=email).first()
    
    if user and bcrypt.check_password_hash(user.password_hash, password):
        return jsonify({
            "message": "Login successful",
            "user": { "email": user.email, "name": user.name, "role": user.role, "state": user.state, "admin_level": user.admin_level }
        }), 200
    
    return jsonify({"message": "Invalid credentials"}), 401


# --- API Routes: Unified Project Management (CRUD + Prediction) ---

@app.route("/api/projects", methods=["GET", "POST"])
@app.route("/api/projects/<int:project_id>", methods=["DELETE", "PUT"])
def project_management(project_id=None):
    
    # ====================================================================
    # GET: Fetch projects
    # ====================================================================
    if request.method == "GET":
        user_email = request.args.get('email')
        
        # 1. Fetch the user who made the request (required for role and state check)
        user = User.query.filter_by(email=user_email).first()
        if not user:
            return jsonify({"message": "User not found or email missing."}), 404
        
        # 2. Filter logic based on user role and admin level
        if user.role == 'admin':
            if user.admin_level == 'state':
                # State Admin View: Return both pending projects in their state AND their own projects
                admin_state = user.state
                cities_in_admin_state = [
                    city for city, state in CITY_TO_STATE_MAP.items() if state == admin_state
                ]
                
                # Get projects in their state with status "pending" (created by employees)
                if cities_in_admin_state:
                    # Get projects that need state admin approval
                    pending_projects = Project.query.filter(
                        Project.location.in_(cities_in_admin_state),
                        Project.status == "pending"
                    )
                    
                    # Get projects created by this state admin
                    admin_projects = Project.query.filter(
                        Project.created_by_email == user_email
                    )
                    
                    # Combine both queries
                    projects = pending_projects.union(admin_projects).all()
                else:
                    # If no cities found for state, just show admin's own projects
                    projects = Project.query.filter_by(created_by_email=user_email).all()
                    
                logging.info(f"State Admin {user_email} fetching {len(projects)} projects for state {admin_state}.")
                
            elif user.admin_level == 'central':
                # Central Admin View: Only show projects that need central approval
                projects = Project.query.filter(
                    Project.status == "pending central approval"
                ).order_by(Project.created_at.desc()).all()
                
                logging.info(f"Central Admin {user_email} fetching {len(projects)} projects pending central approval.")
                
            else:
                # Fallback for admins without admin_level set
                projects = []
                logging.warning(f"Admin {user_email} has no admin_level set.")

        else: # Employee View: Return only projects created by this employee.
            projects = Project.query.filter_by(created_by_email=user_email).all()
            logging.info(f"Employee {user_email} fetching their own {len(projects)} projects.")
        
        # 3. Return the filtered project list
        return jsonify([p.to_dict() for p in projects]), 200

    # ====================================================================
    # PUT: Update project status (Admin action)
    # ====================================================================
    if request.method == "PUT":
        if project_id is None:
            return jsonify({"message": "Missing project ID"}), 400
        
        data = request.get_json()
        new_status = data.get('status')
        email = data.get('email')

        user = User.query.filter_by(email=email).first()
        project_to_update = Project.query.get(project_id)

        if not project_to_update:
            return jsonify({"message": f"Project ID {project_id} not found."}), 404

        # --- ENHANCED AUTHORIZATION LOGIC ---
        # 1. Check if the user is an admin
        if not user or user.role != 'admin':
              return jsonify({"message": "Unauthorized: Only administrators can update project status."}), 403
        
        # 2. Prevent self-approval
        if project_to_update.created_by_email == email:
            return jsonify({"message": "Authorization failed: Cannot approve your own projects."}), 403
        
        # 3. Check authorization based on project status and admin level
        if new_status in ['approved', 'declined']:
            project_state = get_state_from_city(project_to_update.location)
            
            # For projects with status "pending" (created by employees)
            if project_to_update.status == "pending":
                # Only state admins from the same state can approve employee projects
                if user.admin_level != "state" or user.state != project_state:
                    logging.warning(f"Admin {email} (level: {user.admin_level}, state: {user.state}) attempted to action employee project in {project_state}.")
                    return jsonify({"message": "Authorization failed: Only state admins from the same state can approve employee projects."}), 403
            
            # For projects with status "pending central approval" (created by state admins)
            elif project_to_update.status == "pending central approval":
                # Only central admins can approve state admin projects
                if user.admin_level != "central":
                    logging.warning(f"Admin {email} (level: {user.admin_level}) attempted to action state admin project.")
                    return jsonify({"message": "Authorization failed: Only central admins can approve state admin projects."}), 403
                    
            # Update project status
            project_to_update.status = new_status
            try:
                db.session.commit()
                logging.info(f"Project ID {project_id} status updated to {new_status} by admin {email}.")
                return jsonify({"message": f"Project {new_status} successfully"}), 200
            except SQLAlchemyError as e:
                db.session.rollback()
                logging.error(f"Database error updating project status: {e}")
                return jsonify({"message": "Error updating project status"}), 500
        else:
            return jsonify({"message": "Invalid status or unauthorized action"}), 400
        # --- END ENHANCED AUTHORIZATION LOGIC ---

    # ====================================================================
    # POST: Create a new project (Prediction + Save to DB)
    # ====================================================================
    if request.method == "POST":
        data = request.get_json()
        input_features = data.get("input_features")
        project_details = data.get("project_details")
        
        if not input_features or not project_details:
            return jsonify({"error": "Missing input features or project details."}), 400
            
        # Check if user is a central admin
        creator_email = project_details.get('createdBy')
        if creator_email:
            creator = User.query.filter_by(email=creator_email).first()
            if creator and creator.role == 'admin' and creator.admin_level == 'central':
                return jsonify({"error": "Central administrators are not allowed to create projects."}), 403

        # --- DEBUG: Print incoming data before prediction attempt ---
        logging.info(f"Attempting prediction for user: {project_details.get('createdBy')}")
        logging.info(f"Input Features Received: {input_features}")

        # 1. Run Predictions
        all_predictions = {}
        if LOADED_MODELS:
            try:
                for model_name, model_assets in LOADED_MODELS.items():
                    features_ordered = create_feature_vector(input_features, model_assets["columns"])
                    
                    input_array = np.array(features_ordered, dtype=np.float32).reshape(1, -1)
                    scaled_input = model_assets["scaler"].transform(input_array)
                    
                    prediction = model_assets["session"].run(
                        [model_assets["output_name"]], 
                        {model_assets["input_name"]: scaled_input}
                    )
                    all_predictions[model_name] = float(prediction[0].flatten()[0])
            except ValueError as e:
                logging.error(f"Prediction feature error (ValueError): {e}")
                return jsonify({"error": f"Input data formatting failed: {str(e)}"}), 400
            except Exception as e:
                logging.error(f"Prediction process failed unexpectedly: {e}")
                return jsonify({"error": f"Prediction model execution failed: {str(e)}"}), 500
        else:
            return jsonify({"error": "Model assets not loaded on server."}), 503

        # 2. Save Project to Database
        forecast_json_string = json.dumps(all_predictions) 

        # Determine status based on user role and admin level
        creator = User.query.filter_by(email=project_details['createdBy']).first()
        status = 'pending'  # Default for regular users
        
        if creator and creator.role == 'admin':
            if creator.admin_level == 'state':
                status = 'pending central approval'
            elif creator.admin_level == 'central':
                status = 'approved'
        
        # IMPORTANT: Ensure the values passed here match the SQLAlchemy model's constraints (String/length)
        new_project = Project(
            # Pass original input strings/values for database save (no float conversion needed here)
            budget=str(input_features['budget']), # Convert back to string just in case, for DB save
            location=input_features['location'], 
            tower_type=input_features['towerType'],
            substation_type=input_features['substationType'], 
            geo=input_features['geo'], 
            taxes=input_features['taxes'],
            status=status, 
            created_at=project_details['createdAt'], 
            created_by_email=project_details['createdBy'], 
            forecast_data=forecast_json_string
        )
        
        try:
            db.session.add(new_project)
            db.session.commit()
            logging.info(f"Project created successfully with ID: {new_project.id}")
        except SQLAlchemyError as e:
            db.session.rollback()
            logging.error(f"Database error during project save: {e}")
            # If the database save fails (e.g., string too long), this message is returned.
            return jsonify({"message": f"Database error during project save: {str(e)}"}), 500

        # 3. Return the saved project (including DB-assigned ID and forecasts)
        return jsonify({
            "message": "Project created and forecasts generated.",
            "project": new_project.to_dict()
        }), 201

    # ====================================================================
    # DELETE: Delete a project
    # ====================================================================
    if request.method == "DELETE":
        if project_id is None:
            return jsonify({"message": "Missing project ID"}), 400
            
        project_to_delete = Project.query.get(project_id)
        
        if not project_to_delete:
            return jsonify({"message": f"Project ID {project_id} not found."}), 404

        # --- RECOMMENDED AUTHORIZATION CHECK for DELETE ---
        data = request.get_json()
        requester_email = data.get('email')
        requester = User.query.filter_by(email=requester_email).first()

        # Check if the user is the creator OR an admin
        is_creator = project_to_delete.created_by_email == requester_email
        is_admin = requester and requester.role == 'admin'

        if not is_creator and not is_admin:
            logging.warning(f"Unauthorized DELETE attempt on project {project_id} by {requester_email}")
            return jsonify({"message": "Unauthorized to delete this project."}), 403
        # --- END AUTHORIZATION CHECK ---

        try:
            db.session.delete(project_to_delete)
            db.session.commit()
        except SQLAlchemyError as e:
            db.session.rollback()
            logging.error(f"Database error during project deletion: {e}")
            return jsonify({"message": "Database error during project deletion"}), 500
            
        return jsonify({"message": f"Project ID {project_id} deleted."}), 200
    
# --- Application Startup ---
if __name__ == "__main__":
    # Create tables if they don't exist
    with app.app_context():
        logging.info("Creating database tables if they do not exist...")
        db.create_all()
        
    app.run(debug=True, host="0.0.0.0", port=5002)