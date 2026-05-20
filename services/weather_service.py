# KrishiConnect AI - Weather Service (Open-Meteo, FREE, NO API KEY)
import requests
import sqlite3
import json
import time
import concurrent.futures
from datetime import datetime
from config import Config
from database import get_db


class WeatherService:
    """Fetch 7-day weather forecast using Open-Meteo (free, unlimited)"""
    
    def __init__(self):
        self.base_url = Config.OPENMETEO_BASE_URL
    
    def fetch_forecast(self, lat, lon):
        """Fetch 7-day forecast for a single location"""
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": [
                "temperature_2m_max",
                "temperature_2m_min", 
                "precipitation_sum",
                "rain_sum",
                "relative_humidity_2m_mean",
                "wind_speed_10m_max"
            ],
            "timezone": "Asia/Kolkata",
            "forecast_days": 7
        }
        
        try:
            resp = requests.get(self.base_url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            daily = data.get('daily', {})
            dates = daily.get('time', [])
            
            forecast = []
            for i in range(len(dates)):
                forecast.append({
                    'date': dates[i],
                    'temp_max': daily['temperature_2m_max'][i],
                    'temp_min': daily['temperature_2m_min'][i],
                    'rainfall': daily['precipitation_sum'][i] or 0,
                    'rain': daily['rain_sum'][i] or 0,
                    'humidity': daily['relative_humidity_2m_mean'][i] or 0,
                    'wind_speed': daily['wind_speed_10m_max'][i] or 0,
                })
            
            # Calculate summary stats
            summary = self._calculate_summary(forecast)
            
            return {
                'forecast': forecast,
                'summary': summary,
                'location': {'lat': lat, 'lon': lon},
                'fetched_at': datetime.now().isoformat()
            }
        
        except requests.RequestException as e:
            return {'error': str(e), 'forecast': [], 'summary': {}}
    
    def _calculate_summary(self, forecast):
        """Calculate weekly weather summary for disease matching"""
        if not forecast:
            return {}
        
        temps = [(d['temp_max'] + d['temp_min']) / 2 for d in forecast]
        
        return {
            'avg_temp': round(sum(temps) / len(temps), 1),
            'max_temp': max(d['temp_max'] for d in forecast),
            'min_temp': min(d['temp_min'] for d in forecast),
            'avg_humidity': round(sum(d['humidity'] for d in forecast) / len(forecast), 1),
            'total_rainfall': round(sum(d['rainfall'] for d in forecast), 1),
            'rainy_days': sum(1 for d in forecast if d['rainfall'] > 5),
            'avg_wind_speed': round(sum(d['wind_speed'] for d in forecast) / len(forecast), 1),
            'consecutive_wet_days': self._count_consecutive_wet(forecast),
        }
    
    def _count_consecutive_wet(self, forecast):
        """Count max consecutive days with rainfall > 2mm"""
        max_wet = 0
        current = 0
        for d in forecast:
            if d['rainfall'] > 2:
                current += 1
                max_wet = max(max_wet, current)
            else:
                current = 0
        return max_wet
    
    def fetch_and_cache(self, district_id, lat, lon):
        """Fetch weather and store in database cache"""
        # Check if recent cache exists (< 6 hours old)
        conn = get_db()
        cached = conn.execute("""
            SELECT * FROM weather_cache 
            WHERE district_id = ? 
            AND fetched_at > datetime('now', '-6 hours')
            ORDER BY fetched_at DESC LIMIT 1
        """, (district_id,)).fetchone()
        
        if cached:
            conn.close()
            return {
                'forecast': json.loads(cached['forecast_json']),
                'summary': {
                    'avg_temp': cached['avg_temp'],
                    'avg_humidity': cached['avg_humidity'],
                    'total_rainfall': cached['total_rainfall'],
                    'max_temp': cached['max_temp'],
                    'min_temp': cached['min_temp'],
                    'rainy_days': cached['rainy_days'],
                },
                'cached': True
            }
        
        # Fetch fresh data
        result = self.fetch_forecast(lat, lon)
        
        if 'error' not in result and result['summary']:
            s = result['summary']
            conn.execute("""
                INSERT INTO weather_cache 
                (district_id, forecast_json, avg_temp, avg_humidity, 
                 total_rainfall, max_temp, min_temp, rainy_days)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                district_id, json.dumps(result['forecast']),
                s['avg_temp'], s['avg_humidity'], s['total_rainfall'],
                s['max_temp'], s['min_temp'], s['rainy_days']
            ))
            conn.commit()
        
        conn.close()
        result['cached'] = False
        return result
    
    def batch_fetch(self, season=None, max_workers=10):
        """Fetch weather for all active districts in parallel"""
        conn = get_db()
        
        query = """
            SELECT DISTINCT d.id, d.state, d.district, d.latitude, d.longitude
            FROM districts d
            JOIN district_crops dc ON d.id = dc.district_id
            WHERE d.is_active = 1
        """
        params = []
        if season:
            query += " AND dc.season = ?"
            params.append(season)
        
        districts = conn.execute(query, params).fetchall()
        conn.close()
        
        total = len(districts)
        results = {'success': 0, 'failed': 0, 'cached': 0, 'details': []}
        
        def fetch_one(district):
            try:
                data = self.fetch_and_cache(
                    district['id'], district['latitude'], district['longitude']
                )
                return {
                    'district_id': district['id'],
                    'district': district['district'],
                    'state': district['state'],
                    'status': 'cached' if data.get('cached') else 'fetched',
                    'summary': data.get('summary', {}),
                }
            except Exception as e:
                return {
                    'district_id': district['id'],
                    'district': district['district'],
                    'status': 'error',
                    'error': str(e)
                }
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_one, dict(d)): d for d in districts}
            
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                results['details'].append(result)
                
                if result['status'] == 'cached':
                    results['cached'] += 1
                elif result['status'] == 'fetched':
                    results['success'] += 1
                else:
                    results['failed'] += 1
        
        results['total'] = total
        return results
    
    def should_send_weather_alert(self, summary):
        """Check if weather is extreme enough for an alert"""
        alerts = []
        
        if summary.get('total_rainfall', 0) > 100:
            alerts.append({
                'type': 'heavy_rain',
                'message': f"Heavy rainfall expected: {summary['total_rainfall']}mm in 7 days",
                'severity': 'high'
            })
        
        if summary.get('max_temp', 0) > 42:
            alerts.append({
                'type': 'heat_wave',
                'message': f"Extreme heat: {summary['max_temp']}C expected",
                'severity': 'high'
            })
        
        if summary.get('avg_humidity', 0) > 90:
            alerts.append({
                'type': 'high_humidity',
                'message': f"Very high humidity: {summary['avg_humidity']}% - fungal risk",
                'severity': 'medium'
            })
        
        if summary.get('consecutive_wet_days', 0) >= 4:
            alerts.append({
                'type': 'prolonged_rain',
                'message': f"{summary['consecutive_wet_days']} consecutive wet days - disease risk high",
                'severity': 'high'
            })
        
        return alerts
