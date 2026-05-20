# KrishiConnect AI - Disease Prediction Service (ML + Rule-based fallback)
import pickle
import pandas as pd
import numpy as np
import sqlite3
import os
from datetime import datetime
from config import Config
from database import get_db


class DiseasePredictor:
    """Predicts crop diseases using ML model with rule-based fallback"""
    
    def __init__(self):
        self.model = None
        self.encoders = None
        self.feature_cols = None
        self.known_crops = []
        self.known_diseases = []
        self.model_loaded = False
        self._load_model()
    
    def _load_model(self):
        """Load trained ML model from pickle file"""
        model_path = os.path.join(Config.MODEL_DIR, 'disease_model.pkl')
        try:
            with open(model_path, 'rb') as f:
                data = pickle.load(f)
            self.model = data['model']
            self.encoders = data['label_encoders']
            self.feature_cols = data['feature_cols']
            self.known_crops = data.get('crops', [])
            self.known_diseases = data.get('diseases', [])
            self.model_loaded = True
            print(f"[ML] Model loaded: {len(self.known_crops)} crops, {len(self.known_diseases)} diseases, accuracy={data.get('accuracy', 'N/A')}")
        except FileNotFoundError:
            print("[ML] Model file not found. Using rule-based fallback.")
        except Exception as e:
            print(f"[ML] Model load error: {e}. Using rule-based fallback.")
    
    def predict_for_district(self, district_id, weather_summary, season=None):
        """
        Predict diseases for ALL crops in a district given weather data.
        Returns list of predictions sorted by probability.
        """
        conn = get_db()
        
        # Get district info
        district = conn.execute(
            "SELECT * FROM districts WHERE id = ?", (district_id,)
        ).fetchone()
        
        if not district:
            conn.close()
            return []
        
        # Get crops — use provided season or detect current
        if not season:
            season = self._get_current_season()
        crops = conn.execute(
            "SELECT DISTINCT crop FROM district_crops WHERE district_id = ? AND season = ?",
            (district_id, season)
        ).fetchall()
        
        conn.close()
        
        if not crops:
            return []
        
        predictions = []
        for crop_row in crops:
            crop = crop_row['crop']
            crop_predictions = self._predict_crop(
                crop, weather_summary, district, season
            )
            predictions.extend(crop_predictions)
        
        # Sort by probability descending
        predictions.sort(key=lambda x: x['probability'], reverse=True)
        return predictions
    
    def predict_batch(self, districts_weather, season=None):
        """
        Batch predict for multiple districts at once.
        districts_weather = [{district_id, weather_summary}, ...]
        """
        if not season:
            season = self._get_current_season()
        
        conn = get_db()
        all_predictions = []
        
        for dw in districts_weather:
            district_id = dw['district_id']
            weather = dw['weather_summary']
            
            district = conn.execute(
                "SELECT * FROM districts WHERE id = ?", (district_id,)
            ).fetchone()
            
            if not district:
                continue
            
            crops = conn.execute(
                "SELECT DISTINCT crop FROM district_crops WHERE district_id = ? AND season = ?",
                (district_id, season)
            ).fetchall()
            
            for crop_row in crops:
                preds = self._predict_crop(
                    crop_row['crop'], weather, district, season
                )
                all_predictions.extend(preds)
        
        conn.close()
        all_predictions.sort(key=lambda x: x['probability'], reverse=True)
        return all_predictions
    
    def _predict_crop(self, crop, weather_summary, district, season):
        """Predict diseases for a single crop using ML or rules"""
        
        # Normalize crop name for ML (spaces to underscores)
        crop_normalized = crop.replace(' ', '_')
        
        # Try ML first
        if self.model_loaded and crop_normalized in self.known_crops:
            return self._ml_predict(crop_normalized, crop, weather_summary, district, season)
        else:
            # Fallback to rule-based
            return self._rule_predict(crop, weather_summary, district, season)
    
    def _ml_predict(self, crop_normalized, crop_original, weather, district, season):
        """ML-based prediction — checks all diseases for this crop"""
        results = []
        month = datetime.now().month
        season_val = season.lower()
        
        # Determine region type
        region = self._get_region_type(district['latitude'])
        
        for disease in self.known_diseases:
            try:
                row = {
                    'crop_enc': self.encoders['crop'].transform([crop_normalized])[0],
                    'disease_enc': self.encoders['disease'].transform([disease])[0],
                    'season_enc': self.encoders['season'].transform([season_val])[0],
                    'region_type_enc': self.encoders['region_type'].transform([region])[0],
                    'month': month,
                    'avg_temp_C': weather.get('avg_temp', 25),
                    'avg_humidity_pct': weather.get('avg_humidity', 60),
                    'total_rainfall_mm': weather.get('total_rainfall', 0),
                    'soil_moisture_pct': self._estimate_soil_moisture(weather),
                    'wind_speed_kmh': weather.get('avg_wind_speed', 10),
                    'consecutive_wet_days': weather.get('consecutive_wet_days', 0),
                }
                
                df = pd.DataFrame([row])
                prob = self.model.predict_proba(df)[0][1]  # P(disease=1)
                
                if prob > 0.2:  # Include advisory-level risks
                    # 3-tier risk level
                    if prob >= 0.7:
                        risk_level = 'HIGH'
                    elif prob >= 0.45:
                        risk_level = 'MODERATE'
                    else:
                        risk_level = 'ADVISORY'
                    
                    # Get product recommendation
                    product_info = self._get_product(disease)
                    
                    results.append({
                        'district_id': district['id'],
                        'district': district['district'],
                        'state': district['state'],
                        'language': district['language'],
                        'crop': crop_original,
                        'disease': disease,
                        'probability': round(float(prob), 3),
                        'risk_level': risk_level,
                        'method': 'ml',
                        'product': product_info.get('primary_product', ''),
                        'dosage': product_info.get('primary_dosage', ''),
                        'application': product_info.get('primary_application', ''),
                        'is_viral': product_info.get('is_viral', 0),
                        'weather_summary': weather,
                    })
            except ValueError:
                continue  # Unknown encoding value
        
        return results
    
    def _rule_predict(self, crop, weather, district, season):
        """Rule-based fallback for crops not in ML model"""
        results = []
        
        # Rules for different weather conditions
        rules = [
            # Monsoon diseases (high humidity + rain)
            {'disease': 'Rust', 'temp': (15, 25), 'humidity': (70, 100), 'rain': (10, 300),
             'crops': ['Wheat', 'Bajra', 'Barley', 'Gram', 'Jowar', 'Lentil']},
            {'disease': 'Downy_Mildew', 'temp': (15, 25), 'humidity': (85, 100), 'rain': (50, 500),
             'crops': ['Bajra', 'Jowar']},
            {'disease': 'Root_Rot', 'temp': (20, 35), 'humidity': (70, 100), 'rain': (100, 500),
             'crops': ['Gram', 'Lentil']},
            {'disease': 'Blight', 'temp': (18, 30), 'humidity': (75, 100), 'rain': (30, 300),
             'crops': ['Gram', 'Lentil', 'Barley']},
            
            # Summer/Pre-monsoon diseases (high temp, low humidity, low rain)
            {'disease': 'Powdery_Mildew', 'temp': (20, 38), 'humidity': (20, 70), 'rain': (0, 30),
             'crops': ['Wheat', 'Gram', 'Barley', 'Lentil', 'Cotton', 'Sugarcane']},
            {'disease': 'Aphid_Attack', 'temp': (28, 45), 'humidity': (10, 50), 'rain': (0, 20),
             'crops': ['Wheat', 'Cotton', 'Mustard', 'Bajra', 'Jowar', 'Sugarcane', 'Groundnut']},
            {'disease': 'Whitefly_Infestation', 'temp': (32, 48), 'humidity': (10, 45), 'rain': (0, 10),
             'crops': ['Cotton', 'Sugarcane', 'Soybean', 'Groundnut', 'Bajra']},
            {'disease': 'Heat_Stress', 'temp': (38, 50), 'humidity': (5, 40), 'rain': (0, 5),
             'crops': ['Wheat', 'Rice', 'Maize', 'Cotton', 'Sugarcane', 'Soybean', 'Gram', 'Bajra', 'Jowar', 'Groundnut', 'Barley', 'Lentil']},
        ]
        
        avg_temp = weather.get('avg_temp', 25)
        avg_humidity = weather.get('avg_humidity', 60)
        total_rain = weather.get('total_rainfall', 0)
        
        for rule in rules:
            if crop not in rule['crops']:
                continue
            
            # Calculate weighted probability based on how deep into danger zone
            temp_score = self._range_score(avg_temp, rule['temp'][0], rule['temp'][1])
            hum_score = self._range_score(avg_humidity, rule['humidity'][0], rule['humidity'][1])
            rain_score = self._range_score(total_rain, rule['rain'][0], rule['rain'][1])
            
            # Weighted average: temp 40%, humidity 35%, rain 25%
            probability = temp_score * 0.40 + hum_score * 0.35 + rain_score * 0.25
            
            if probability >= 0.2:
                # 3-tier risk level
                if probability >= 0.7:
                    risk_level = 'HIGH'
                elif probability >= 0.45:
                    risk_level = 'MODERATE'
                else:
                    risk_level = 'ADVISORY'
                
                product_info = self._get_product(rule['disease'])
                
                results.append({
                    'district_id': district['id'],
                    'district': district['district'],
                    'state': district['state'],
                    'language': district['language'],
                    'crop': crop,
                    'disease': rule['disease'],
                    'probability': round(probability, 3),
                    'risk_level': risk_level,
                    'method': 'rules',
                    'product': product_info.get('primary_product', ''),
                    'dosage': product_info.get('primary_dosage', ''),
                    'application': product_info.get('primary_application', ''),
                    'is_viral': product_info.get('is_viral', 0),
                    'weather_summary': weather,
                })
        
        return results
    
    def _range_score(self, value, low, high):
        """Score 0-1 based on how deep a value is inside a range.
        0 = outside range, 0.5 = at edge, 1.0 = at midpoint."""
        if value < low or value > high:
            return 0.0
        mid = (low + high) / 2
        half_range = (high - low) / 2
        if half_range == 0:
            return 1.0
        distance_from_mid = abs(value - mid)
        return round(1.0 - (distance_from_mid / half_range) * 0.5, 3)
    
    def _get_product(self, disease_name):
        """Get Syngenta product recommendation for a disease.
        Safeguard: if disease not in map, returns a sensible fallback product
        so campaigns never go out without a product recommendation.
        """
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM disease_product_map WHERE disease_name = ?",
            (disease_name,)
        ).fetchone()
        conn.close()
        
        if row:
            return dict(row)
        
        # ── Fallback: disease not in map ──
        # Determine type from name and assign best-fit product
        name_lower = disease_name.lower()
        pest_keywords = ['attack', 'worm', 'fly', 'insect', 'mite', 'borer', 'hopper', 'beetle', 'aphid', 'thrips']
        
        if any(k in name_lower for k in pest_keywords):
            # Pest/Insect -> Actara (systemic insecticide)
            print(f"[PRODUCT] Fallback: {disease_name} -> Actara (pest)")
            return {
                'primary_product': 'Actara', 'primary_chemical': 'Thiamethoxam',
                'primary_dosage': '100g/acre', 'primary_application': 'Foliar spray',
                'is_viral': 0, 'disease_type': 'Pest/Insect',
            }
        elif 'virus' in name_lower or 'mosaic' in name_lower or 'curl' in name_lower:
            # Viral -> Actara (vector control)
            print(f"[PRODUCT] Fallback: {disease_name} -> Actara (viral vector)")
            return {
                'primary_product': 'Actara', 'primary_chemical': 'Thiamethoxam',
                'primary_dosage': '100g/acre', 'primary_application': 'Foliar spray',
                'is_viral': 1, 'disease_type': 'Viral',
            }
        elif 'stress' in name_lower or 'drought' in name_lower:
            # Abiotic -> Isabion (biostimulant)
            print(f"[PRODUCT] Fallback: {disease_name} -> Isabion (stress)")
            return {
                'primary_product': 'Isabion', 'primary_chemical': 'Amino acids',
                'primary_dosage': '400ml/acre', 'primary_application': 'Foliar spray',
                'is_viral': 0, 'disease_type': 'Abiotic',
            }
        else:
            # Default fungal -> Amistar Top (broadest spectrum fungicide)
            print(f"[PRODUCT] Fallback: {disease_name} -> Amistar Top (default)")
            return {
                'primary_product': 'Amistar Top', 'primary_chemical': 'Azoxystrobin + Difenoconazole',
                'primary_dosage': '200ml/acre', 'primary_application': 'Foliar spray',
                'is_viral': 0, 'disease_type': 'Fungal',
            }
    
    def _get_current_season(self):
        """Determine current agricultural season based on month.
        Kharif: May-Oct (includes pre-monsoon prep from May)
        Rabi: Nov-Apr (winter crops)
        Note: 'Summer' season has very few DB entries (88 vs Kharif's 4030),
        so May is mapped to Kharif for practical advisory coverage.
        """
        month = datetime.now().month
        if month in [5, 6, 7, 8, 9, 10]:
            return 'Kharif'
        else:
            return 'Rabi'
    
    def _get_region_type(self, latitude):
        """Estimate region type from latitude"""
        if latitude < 12:
            return 'coastal'
        elif latitude < 20:
            return 'plains'
        elif latitude < 25:
            return 'inland'
        elif latitude < 30:
            return 'plains'
        else:
            return 'hilly'
    
    def _estimate_soil_moisture(self, weather):
        """Estimate soil moisture from rainfall and humidity"""
        rain = weather.get('total_rainfall', 0)
        humidity = weather.get('avg_humidity', 50)
        wet_days = weather.get('consecutive_wet_days', 0)
        return min(100, rain * 0.3 + humidity * 0.3 + wet_days * 5)
