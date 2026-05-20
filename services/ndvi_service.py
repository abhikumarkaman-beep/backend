# KrishiConnect AI - NDVI / Crop Health Monitoring Service
# REAL satellite flow: Pixels → Nearest District → 3-Case Classification
# 3-Tier Data: NASA MODIS → Weather VHI → Demo Mode
import math
import json
import random
import requests
import concurrent.futures
from datetime import datetime, timedelta
from config import Config
from database import get_db
from services.weather_service import WeatherService


class NDVIService:
    """NDVI crop health monitoring with nearest-district matching"""
    
    NASA_MODIS_URL = "https://modis.ornl.gov/rst/api/v1/MOD13Q1/subset"
    NASA_TIMEOUT = 5
    
    def __init__(self):
        self.weather_service = WeatherService()
        self.districts_cache = None
        self.nasa_available = None  # None = untested
        self.norms = {
            'kharif': {'temp_min': 22, 'temp_max': 42, 'temp_optimal': 30,
                       'rain_adequate': 50, 'humidity_optimal': 65},
            'rabi':   {'temp_min': 8, 'temp_max': 35, 'temp_optimal': 22,
                       'rain_adequate': 20, 'humidity_optimal': 55},
        }
    
    def _get_current_season(self):
        """Determine current agricultural season.
        Kharif: May-Oct (includes pre-monsoon sowing prep from May)
        Rabi: Nov-Apr (winter crops)
        Note: Zaid removed — almost no Zaid crop data exists in DB.
        Matches disease_service._get_current_season() for consistency.
        """
        month = datetime.now().month
        if month in [5, 6, 7, 8, 9, 10]:
            return 'Kharif'
        return 'Rabi'
    
    def _load_districts(self):
        """Load all districts with coordinates for Haversine matching"""
        if self.districts_cache:
            return self.districts_cache
        conn = get_db()
        rows = conn.execute(
            "SELECT id, state, district, latitude, longitude FROM districts"
        ).fetchall()
        conn.close()
        self.districts_cache = [dict(r) for r in rows]
        return self.districts_cache
    
    def _haversine(self, lat1, lon1, lat2, lon2):
        """Calculate distance in km between two points on Earth"""
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat/2)**2 +
             math.cos(math.radians(lat1)) *
             math.cos(math.radians(lat2)) *
             math.sin(dlon/2)**2)
        return R * 2 * math.asin(math.sqrt(a))
    
    def find_nearest_district(self, lat, lon):
        """
        Given any coordinate (from NDVI pixel), find the nearest district.
        Uses Haversine formula for accurate Earth-surface distance.
        """
        districts = self._load_districts()
        best_match = None
        min_distance = float('inf')
        
        for d in districts:
            distance = self._haversine(lat, lon, d['latitude'], d['longitude'])
            if distance < min_distance:
                min_distance = distance
                best_match = d
        
        return best_match, round(min_distance, 1)
    
    # ═══════════════════════════════════════
    # TIER 1: NASA MODIS Real Satellite NDVI
    # ═══════════════════════════════════════
    def fetch_nasa_ndvi(self, lat, lon):
        """Fetch REAL NDVI from NASA MODIS satellite (MOD13Q1)."""
        try:
            now = datetime.now()
            start = now - timedelta(days=32)
            start_str = f"A{start.year}{start.strftime('%j')}"
            end_str = f"A{now.year}{now.strftime('%j')}"
            
            resp = requests.get(self.NASA_MODIS_URL, params={
                'latitude': round(lat, 4),
                'longitude': round(lon, 4),
                'band': '250m_16_days_NDVI',
                'startDate': start_str,
                'endDate': end_str,
                'kmAboveBelow': 0,
                'kmLeftRight': 0,
            }, timeout=self.NASA_TIMEOUT)
            
            if resp.status_code != 200:
                return None
            
            data = resp.json()
            subsets = data.get('subset', [])
            if not subsets:
                return None
            
            latest = subsets[-1]
            values = latest.get('data', [])
            valid = [v for v in values if -2000 <= v <= 10000]
            if not valid:
                return None
            
            avg_raw = sum(valid) / len(valid)
            ndvi = round(max(0.0, min(1.0, avg_raw * 0.0001)), 3)
            return ndvi
            
        except (requests.Timeout, requests.ConnectionError):
            return None
        except Exception:
            return None
    
    # ═══════════════════════════════════════
    # TIER 2: Weather-Derived VHI
    # ═══════════════════════════════════════
    def calculate_vhi_ndvi(self, weather_summary, season=None):
        """Calculate NDVI-equivalent from real weather using VHI methodology."""
        if not weather_summary:
            return None
        
        if not season:
            season = self._get_current_season()
        norms = self.norms.get(season.lower(), self.norms['kharif'])
        
        avg_temp = weather_summary.get('avg_temp', 30)
        avg_humidity = weather_summary.get('avg_humidity', 50)
        total_rainfall = weather_summary.get('total_rainfall', 0)
        max_temp = weather_summary.get('max_temp', avg_temp + 5)
        
        # TCI: Temperature Condition Index
        temp_deviation = abs(avg_temp - norms['temp_optimal'])
        temp_range = norms['temp_max'] - norms['temp_min']
        tci = max(0, min(100, 100 - (temp_deviation / temp_range * 200)))
        if max_temp > norms['temp_max']:
            tci = max(0, tci - (max_temp - norms['temp_max']) * 5)
        
        # MCI: Moisture Condition Index
        humidity_score = max(0, min(100, avg_humidity / norms['humidity_optimal'] * 100))
        if avg_humidity > 85:
            humidity_score = max(40, humidity_score - (avg_humidity - 85) * 2)
        rain_score = min(100, total_rainfall / max(1, norms['rain_adequate']) * 100)
        if total_rainfall > norms['rain_adequate'] * 3:
            rain_score = max(30, rain_score - 30)
        
        mci = humidity_score * 0.6 + rain_score * 0.4
        vhi = 0.5 * tci + 0.5 * mci
        
        return round(max(0.05, min(0.95, vhi / 100)), 2)
    
    # ═══════════════════════════════════════
    # TIER 3: Demo Mode (last resort)
    # ═══════════════════════════════════════
    def generate_demo_ndvi(self, lat, lon):
        """Simulated NDVI for offline demo — consistent per location."""
        seed = int((lat * 1000 + lon * 100) % 10000)
        rng = random.Random(seed)
        if rng.random() < 0.3:
            return round(rng.uniform(0.1, 0.35), 2)
        return round(rng.uniform(0.45, 0.85), 2)
    
    # ═══════════════════════════════════════
    # CORE: Process a single NDVI pixel
    # ═══════════════════════════════════════
    def _classify_stress(self, ndvi_value):
        if ndvi_value >= 0.6:
            return 'healthy'
        elif ndvi_value >= 0.4:
            return 'moderate'
        elif ndvi_value >= 0.2:
            return 'stressed'
        return 'severe'
    
    def check_ndvi_point(self, lat, lon, ndvi_value, method='unknown'):
        """
        Process a single NDVI pixel point:
        1. Find nearest district (Haversine)
        2. 3-Case classification
        3. Save to DB
        
        Case 1: District + Season crops → full crop disease alert
        Case 2: District + NO season crops → vegetation stress alert  
        Case 3: No district within 100km → unmonitored area alert
        """
        district, distance_km = self.find_nearest_district(lat, lon)
        stress_level = self._classify_stress(ndvi_value)
        season = self._get_current_season()
        
        # ── Case 3: No district within 100km ──
        if not district or distance_km > 100:
            return {
                'status': 'unmonitored',
                'case': 3,
                'alert_type': 'unmonitored_area',
                'coordinates': {'lat': lat, 'lon': lon},
                'ndvi_value': ndvi_value,
                'stress_level': stress_level,
                'method': method,
                'district': None,
                'distance_km': distance_km,
                'has_crops': False,
                'crops': [],
                'message': f'No district within 100km — unmonitored area at ({lat:.2f}, {lon:.2f})',
            }
        
        # Get crops for this district in current season
        conn = get_db()
        crops = conn.execute("""
            SELECT crop, season FROM district_crops 
            WHERE district_id = ? AND season = ?
        """, (district['id'], season)).fetchall()
        
        # Also get all crops (any season)
        all_crops = conn.execute("""
            SELECT crop FROM district_crops WHERE district_id = ?
        """, (district['id'],)).fetchall()
        
        has_season_crops = len(crops) > 0
        crop_list = [c['crop'] for c in crops] if crops else []
        all_crop_list = [c['crop'] for c in all_crops] if all_crops else []
        
        # ── Case 2: District exists but NO crops this season ──
        if not has_season_crops:
            alert_type = 'vegetation_stress'
            message = (f'{district["district"]} ({district["state"]}): '
                      f'NDVI {ndvi_value:.2f} ({stress_level}) detected, '
                      f'but no active {season} crop in database. '
                      f'Manual verification needed.')
            case = 2
        # ── Case 1: District + Season crops → normal flow ──
        else:
            if stress_level in ['stressed', 'severe']:
                alert_type = 'crop_disease_risk'
                message = (f'{district["district"]} ({district["state"]}): '
                          f'NDVI {ndvi_value:.2f} — crops at risk: {", ".join(crop_list)}')
            else:
                alert_type = 'healthy_crop'
                message = (f'{district["district"]} ({district["state"]}): '
                          f'NDVI {ndvi_value:.2f} — crops healthy: {", ".join(crop_list)}')
            case = 1
        
        # Save to DB
        conn.execute("""
            INSERT INTO ndvi_alerts 
            (latitude, longitude, ndvi_value, stress_level, 
             matched_district_id, matched_distance_km, has_active_crop, alert_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            lat, lon, ndvi_value, stress_level,
            district['id'], distance_km, 1 if has_season_crops else 0,
            alert_type
        ))
        conn.commit()
        conn.close()
        
        return {
            'status': 'alert' if stress_level in ['stressed', 'severe'] else 'ok',
            'case': case,
            'alert_type': alert_type,
            'coordinates': {'lat': lat, 'lon': lon},
            'ndvi_value': ndvi_value,
            'stress_level': stress_level,
            'method': method,
            'district': district,
            'distance_km': distance_km,
            'has_crops': has_season_crops,
            'crops': crop_list,
            'all_crops': all_crop_list,
            'message': message,
        }
    
    # ═══════════════════════════════════════
    # MAIN: Full scan with 3-tier + 3-case
    # ═══════════════════════════════════════
    def scan_area(self, state=None, num_points=30):
        """
        Full satellite scan simulation:
        1. Generate pixel coordinates (from district locations with offset)
        2. Get NDVI for each pixel (NASA → VHI → Demo)
        3. Match each pixel to nearest district (Haversine)
        4. 3-case classification
        5. Save to DB with dedup
        """
        conn = get_db()
        
        # Get district coordinates as scan targets
        query = "SELECT DISTINCT latitude, longitude, state, district, id FROM districts"
        params = []
        if state:
            query += " WHERE state = ?"
            params.append(state)
        query += f" ORDER BY RANDOM() LIMIT {int(num_points)}"
        
        sample_districts = conn.execute(query, params).fetchall()
        conn.close()
        
        if not sample_districts:
            return {'total_points': 0, 'points': [], 'stressed_points': 0,
                    'healthy_points': 0, 'scan_type': '3-tier', 'state_filter': state}
        
        # 7-day dedup: check which districts already scanned
        conn = get_db()
        seven_days_ago = (datetime.now() - timedelta(days=7)).isoformat()
        existing = conn.execute("""
            SELECT matched_district_id FROM ndvi_alerts
            WHERE created_at > ? AND matched_district_id IS NOT NULL
        """, (seven_days_ago,)).fetchall()
        conn.close()
        existing_ids = set(r['matched_district_id'] for r in existing)
        
        # Filter out already-scanned districts
        to_scan = [dict(d) for d in sample_districts if d['id'] not in existing_ids]
        already_cached = len(sample_districts) - len(to_scan)
        
        if not to_scan:
            return {'total_points': 0, 'points': [], 'stressed_points': 0,
                    'healthy_points': 0, 'cached_skipped': already_cached,
                    'scan_type': '3-tier', 'state_filter': state,
                    'message': f'All {already_cached} districts scanned within 7 days'}
        
        # ── Step 1: Test NASA availability (single quick test) ──
        test_pixel = to_scan[0]
        nasa_result = self.fetch_nasa_ndvi(test_pixel['latitude'], test_pixel['longitude'])
        self.nasa_available = nasa_result is not None
        
        # ── Step 2: If NASA down, fetch weather in parallel for VHI fallback ──
        weather_map = {}
        season = self._get_current_season()
        
        if not self.nasa_available:
            def fetch_weather(dist):
                try:
                    w = self.weather_service.fetch_and_cache(
                        dist['id'], dist['latitude'], dist['longitude']
                    )
                    return (dist['id'], w)
                except:
                    return (dist['id'], {})
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(fetch_weather, d): d for d in to_scan}
                for future in concurrent.futures.as_completed(futures):
                    did, w = future.result()
                    weather_map[did] = w
        
        # ── Step 3: Generate pixel points and get NDVI ──
        results = []
        stress_count = 0
        healthy_count = 0
        method_counts = {'nasa_modis': 0, 'weather_vhi': 0, 'demo_simulated': 0}
        
        for i, d in enumerate(to_scan):
            # Add random offset to simulate satellite pixel (not exact district center)
            offset_lat = random.uniform(-0.15, 0.15)
            offset_lon = random.uniform(-0.15, 0.15)
            pixel_lat = d['latitude'] + offset_lat
            pixel_lon = d['longitude'] + offset_lon
            
            # ── 3-Tier NDVI value ──
            ndvi_value = None
            method = 'unknown'
            
            # Tier 1: NASA MODIS
            if self.nasa_available:
                if i == 0 and nasa_result is not None:
                    ndvi_value = nasa_result  # Reuse test result for first pixel
                else:
                    ndvi_value = self.fetch_nasa_ndvi(pixel_lat, pixel_lon)
                if ndvi_value is not None:
                    method = 'nasa_modis'
            
            # Tier 2: Weather VHI (if NASA failed)
            if ndvi_value is None:
                weather = weather_map.get(d['id'], {})
                summary = weather.get('summary')
                if summary:
                    ndvi_value = self.calculate_vhi_ndvi(summary, season)
                    if ndvi_value is not None:
                        method = 'weather_vhi'
            
            # Tier 3: Demo mode (last resort)
            if ndvi_value is None:
                ndvi_value = self.generate_demo_ndvi(pixel_lat, pixel_lon)
                method = 'demo_simulated'
            
            method_counts[method] = method_counts.get(method, 0) + 1
            
            # ── Process pixel: nearest district + 3-case logic ──
            result = self.check_ndvi_point(pixel_lat, pixel_lon, ndvi_value, method)
            
            # Add weather info to result
            weather = weather_map.get(d['id'], {})
            summary = weather.get('summary')
            if summary:
                result['weather'] = {
                    'temp': summary.get('avg_temp'),
                    'humidity': summary.get('avg_humidity'),
                    'rainfall': summary.get('total_rainfall'),
                }
            
            result['scanned_at'] = datetime.now().isoformat()
            results.append(result)
            
            if result['stress_level'] in ['stressed', 'severe']:
                stress_count += 1
            else:
                healthy_count += 1
        
        # Method label
        primary = max(method_counts, key=method_counts.get) if method_counts else 'unknown'
        method_labels = {
            'nasa_modis': 'NASA MODIS Satellite (Real NDVI)',
            'weather_vhi': 'Weather-derived VHI (Real Weather Data)',
            'demo_simulated': '⚠️ Demo Mode (NASA & Weather unavailable)',
        }
        
        return {
            'scan_type': '3-tier-fallback',
            'method': method_labels.get(primary, primary),
            'method_breakdown': method_counts,
            'nasa_available': self.nasa_available,
            'total_points': len(results),
            'stressed_points': stress_count,
            'healthy_points': healthy_count,
            'moderate_points': len([r for r in results if r['stress_level'] == 'moderate']),
            'cached_skipped': already_cached,
            'state_filter': state,
            'season': season,
            'case_breakdown': {
                'case_1_crop_monitored': len([r for r in results if r.get('case') == 1]),
                'case_2_no_season_crop': len([r for r in results if r.get('case') == 2]),
                'case_3_unmonitored': len([r for r in results if r.get('case') == 3]),
            },
            'points': results,
            'scanned_at': datetime.now().isoformat(),
        }
    
    # Legacy alias
    def simulate_ndvi_scan(self, state=None, num_points=20):
        return self.scan_area(state=state, num_points=num_points)
    
    def get_alerts(self, stress_level=None, state=None, limit=200):
        """Get NDVI alerts from database with state filter"""
        conn = get_db()
        query = """
            SELECT n.*, d.state, d.district as district_name
            FROM ndvi_alerts n
            LEFT JOIN districts d ON n.matched_district_id = d.id
            WHERE 1=1
        """
        params = []
        if stress_level:
            query += " AND n.stress_level = ?"
            params.append(stress_level)
        if state:
            query += " AND d.state = ?"
            params.append(state)
        query += f" ORDER BY n.created_at DESC LIMIT {int(limit)}"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    
    def get_ndvi_heatmap_data(self, state=None):
        """Get all NDVI data points for heatmap — enriched with weather"""
        conn = get_db()
        query = """
            SELECT n.latitude, n.longitude, n.ndvi_value, n.stress_level,
                   n.alert_type, n.has_active_crop, n.matched_distance_km,
                   n.matched_district_id, n.created_at,
                   d.state, d.district as district_name
            FROM ndvi_alerts n
            LEFT JOIN districts d ON n.matched_district_id = d.id
        """
        params = []
        if state:
            query += " WHERE d.state = ?"
            params.append(state)
        query += " ORDER BY n.created_at DESC LIMIT 500"
        rows = conn.execute(query, params).fetchall()
        
        # Fetch weather from cache for each district
        points = []
        for r in rows:
            p = dict(r)
            if p.get('matched_district_id'):
                weather_row = conn.execute("""
                    SELECT weather_data FROM weather_cache
                    WHERE district_id = ?
                    ORDER BY fetched_at DESC LIMIT 1
                """, (p['matched_district_id'],)).fetchone()
                if weather_row:
                    import json
                    try:
                        wd = json.loads(weather_row['weather_data'])
                        summary = wd.get('summary', {})
                        p['weather'] = {
                            'temp': summary.get('avg_temp'),
                            'humidity': summary.get('avg_humidity'),
                            'rainfall': summary.get('total_rainfall'),
                        }
                    except:
                        pass
            points.append(p)
        
        conn.close()
        return points
