import os
import json
import boto3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from botocore.exceptions import ClientError

Base = declarative_base()


def get_secret(secret_name: str, region_name: str = None) -> dict:
    """Retrieve secret from AWS Secrets Manager.
    
    Args:
        secret_name: Name or ARN of the secret
        region_name: AWS region (defaults to AWS_REGION env var or us-east-1)
    
    Returns:
        Dictionary containing secret values
    
    Raises:
        ClientError: If secret cannot be retrieved
    """
    if region_name is None:
        region_name = os.getenv('AWS_REGION', 'us-east-1')
    
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )
    
    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
    except ClientError as e:
        raise e
    
    secret = get_secret_value_response['SecretString']
    return json.loads(secret)


def get_database_password() -> str:
    """Get database password from AWS Secrets Manager or environment.
    
    Returns:
        Database password string
    """
    # Check if we should use Secrets Manager
    secret_arn = os.getenv('DB_SECRET_ARN')
    
    if secret_arn:
        # Production/staging: use AWS Secrets Manager
        try:
            secret_data = get_secret(secret_arn)
            return secret_data.get('password', secret_data.get('POSTGRES_PASSWORD'))
        except ClientError as e:
            # Log error and fall back to environment variable
            print(f"Warning: Failed to retrieve secret from Secrets Manager: {e}")
    
    # Development/local: use environment variable with no default
    password = os.getenv('POSTGRES_PASSWORD')
    if not password:
        raise ValueError(
            "Database password not configured. Set POSTGRES_PASSWORD env var "
            "or DB_SECRET_ARN for Secrets Manager integration."
        )
    return password


def get_database_url() -> str:
    """Construct database URL from environment variables and Secrets Manager.
    
    Returns:
        SQLAlchemy database URL
    """
    db_user = os.getenv('POSTGRES_USER', 'ortobahn')
    db_host = os.getenv('POSTGRES_HOST', 'localhost')
    db_port = os.getenv('POSTGRES_PORT', '5432')
    db_name = os.getenv('POSTGRES_DB', 'ortobahn')
    db_password = get_database_password()
    
    return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"


# Create engine and session
engine = create_engine(
    get_database_url(),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Dependency for getting database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables."""
    Base.metadata.create_all(bind=engine)
