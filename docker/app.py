from flask import Flask, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import text
import boto3
import json
import logging
import os
import psycopg2

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
# Set up logging
app = Flask(__name__)

# Fetch secret from AWS Secrets Manager
def get_db_credentials():
    secret_name = os.environ.get('DB_SECRET_ARN')
    region_name = os.environ.get('AWS_REGION', 'eu-central-1')
    
    client = boto3.client('secretsmanager', region_name=region_name)
    response = client.get_secret_value(SecretId=secret_name)
    secret = json.loads(response['SecretString'])
    return secret['username'], secret['password']

def create_database_if_not_exists():
    """Create the database if it doesn't exist"""
    username, password = get_db_credentials()
    db_host = os.environ.get('DB_HOST')
    
    try:
        # Connect to the default postgres database
        conn = psycopg2.connect(
            host=db_host,
            port=5432, 
            user=username,
            password=password,
            database="postgres"
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Check if our database exists
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = 'flaskdb'")
        if not cursor.fetchone():
            logger.info("Creating flaskdb database")
            cursor.execute("CREATE DATABASE flaskdb")
            logger.info("Database created successfully")
        else:
            logger.info("Database flaskdb already exists")
            
        cursor.close()
        conn.close()
        
        # Now connect to our database to create tables
        conn = psycopg2.connect(
            host=db_host,
            port=5432,
            user=username,
            password=password,
            database="flaskdb"
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        # Create necessary tables
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS "user" (
            id SERIAL PRIMARY KEY,
            username VARCHAR(80) UNIQUE NOT NULL,
            email VARCHAR(120) UNIQUE NOT NULL
        )
        """)
        
        cursor.close()
        conn.close()
        logger.info("Database initialization complete")
        return True
        
    except Exception as e:
        logger.error(f"Database initialization error: {str(e)}")
        # Don't crash on database creation error
        return False


# Try to create database on startup
try:
    create_database_if_not_exists()
except Exception as e:
    logger.warning(f"Could not create database during startup: {str(e)}")

username, password = get_db_credentials()
db_host = os.environ.get('DB_HOST')
app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql://{username}:{password}@{db_host}:5432/flaskdb"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)

    def __repr__(self):
        return f'<User {self.username}>'

@app.route('/ping')
def ping():
    return jsonify({'status': 'container running'}), 200

@app.route('/')
def health_check():
    try:
        # Try standard db check
        db.session.execute(text('SELECT 1'))
        app.logger.debug('Database connection successful')
        return jsonify({'status': 'healthy', 'database': 'connected'}), 200
    except Exception as e:
        error_msg = str(e)
        app.logger.error(f"Health check failed: {error_msg}")
        
        # If database doesn't exist, try to create it on-demand
        if "does not exist" in error_msg:
            try:
                if create_database_if_not_exists():
                    return jsonify({'status': 'initializing', 'message': 'Database created, restarting connection'}), 200
            except Exception as creation_error:
                logger.error(f"On-demand database creation failed: {str(creation_error)}")
                
        return jsonify({'status': 'unhealthy', 'error': error_msg}), 503

if __name__ == '__main__':
    # Initialize database tables
    with app.app_context():
        try:
            db.create_all()
            logger.info("Tables created successfully")
        except Exception as e:
            logger.error(f"Error creating tables: {str(e)}")
    
    app.run(host='0.0.0.0', port=5000)
