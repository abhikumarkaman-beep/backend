# KrishiConnect AI - Backend Configuration
import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Application configuration"""
    
    # Database
    DATABASE_PATH = os.getenv('DATABASE_PATH', os.path.join(os.path.dirname(__file__), 'krishiconnect.db'))
    
    # API Keys
    # Gemini: supports multiple keys for rotation (comma-separated in .env)
    GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
    GEMINI_API_KEYS = [k.strip() for k in os.getenv('GEMINI_API_KEYS', os.getenv('GEMINI_API_KEY', '')).split(',') if k.strip()]
    
    # Twilio: supports multiple accounts for rotation (SID:TOKEN:SANDBOX_CODE,SID2:TOKEN2:CODE2)
    TWILIO_ACCOUNTS = []
    _raw_accounts = os.getenv('TWILIO_ACCOUNTS', '')
    if _raw_accounts:
        for entry in _raw_accounts.split(','):
            parts = entry.strip().split(':')
            if len(parts) >= 2:
                TWILIO_ACCOUNTS.append({
                    'sid': parts[0].strip(),
                    'token': parts[1].strip(),
                    'sandbox_code': ':'.join(parts[2:]).strip() if len(parts) > 2 else '',
                })
    # Backward compat
    TWILIO_ACCOUNT_SID = TWILIO_ACCOUNTS[0]['sid'] if TWILIO_ACCOUNTS else os.getenv('TWILIO_ACCOUNT_SID', '')
    TWILIO_AUTH_TOKEN = TWILIO_ACCOUNTS[0]['token'] if TWILIO_ACCOUNTS else os.getenv('TWILIO_AUTH_TOKEN', '')
    TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER', 'whatsapp:+14155238886')
    
    # Demo retailer number (for live hackathon notification demo)
    DEMO_RETAILER_NUMBER = os.getenv('DEMO_RETAILER_NUMBER', '')
    
    # Open-Meteo (NO KEY NEEDED)
    OPENMETEO_BASE_URL = "https://api.open-meteo.com/v1/forecast"
    
    # Cloudinary (media hosting)
    CLOUDINARY_CLOUD_NAME = os.getenv('CLOUDINARY_CLOUD_NAME', '')
    CLOUDINARY_API_KEY = os.getenv('CLOUDINARY_API_KEY', '')
    CLOUDINARY_API_SECRET = os.getenv('CLOUDINARY_API_SECRET', '')
    
    # Edge TTS (NO KEY NEEDED) - Voice mapping
    VOICE_MAP = {
        'Hindi': 'hi-IN-SwaraNeural',
        'Tamil': 'ta-IN-PallaviNeural',
        'Telugu': 'te-IN-ShrutiNeural',
        'Marathi': 'mr-IN-AarohiNeural',
        'Bengali': 'bn-IN-TanishaaNeural',
        'Kannada': 'kn-IN-SapnaNeural',
        'Gujarati': 'gu-IN-DhwaniNeural',
        'Malayalam': 'ml-IN-SobhanaNeural',
        'Punjabi': 'pa-IN-OjasNeural',
        'Odia': 'or-IN-SubhasiniNeural',
        'Assamese': 'as-IN-PriyomNeural',
        'Urdu': 'ur-IN-GulNeural',
        'Konkani': 'hi-IN-SwaraNeural',  # Fallback to Hindi
        'Mizo': 'hi-IN-SwaraNeural',
        'Bhojpuri': 'hi-IN-SwaraNeural',
    }
    
    # Campaign Settings
    COOLDOWNS = {
        'sms': 24,              # hours (production schedule)
        'whatsapp_text': 48,
        'whatsapp_voice': 168,  # 7 days
        'whatsapp_poster': 48,
    }
    
    # Days before alerting same district for a *different* disease (same disease always blocked)
    DISEASE_REPEAT_DAYS = 0
    
    # Pipeline cache reuse window (hours) for re-runs
    PIPELINE_CACHE_HOURS = int(os.getenv('PIPELINE_CACHE_HOURS', '24'))
    PIPELINE_MAX_WORKERS = int(os.getenv('PIPELINE_MAX_WORKERS', '8'))
    
    # Paths
    STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')
    AUDIO_DIR = os.path.join(STATIC_DIR, 'audio')
    POSTER_DIR = os.path.join(STATIC_DIR, 'posters')
    FONT_DIR = os.path.join(STATIC_DIR, 'fonts')
    MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')
