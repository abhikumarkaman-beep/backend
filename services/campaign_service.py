# KrishiConnect AI - Campaign Service (Scheduler + Delivery + Cooldowns)
import json
import uuid
import sqlite3
import threading
import concurrent.futures
from datetime import datetime, timedelta
from config import Config
from database import get_db
from services.content_service import ContentService, VoiceService
from services.weather_service import WeatherService
from services.disease_service import DiseasePredictor


class CampaignService:
    """Manages campaign lifecycle: predict -> generate content -> deliver"""
    
    _pipeline_lock = threading.Lock()
    _pipeline_running = False
    
    def __init__(self):
        self.content_service = ContentService()
        self.voice_service = VoiceService()
        self.weather_service = WeatherService()
        self.predictor = DiseasePredictor()
    
    def is_pipeline_running(self):
        return CampaignService._pipeline_running
    
    def run_pipeline(self, season=None, state=None, limit=1000, use_simulated_weather=False):
        """
        Full pipeline: Weather -> Predict -> Content -> Campaign
        Returns ALL district statuses — healthy districts + at-risk campaigns.
        One district at a time: weather -> predict -> campaign.
        """
        # Prevent duplicate runs
        if CampaignService._pipeline_running:
            return {'error': 'Pipeline already running! Please wait.'}
        
        CampaignService._pipeline_running = True
        
        try:
            if not season:
                season = self.predictor._get_current_season()
            
            batch_id = str(uuid.uuid4())[:8]
            conn = get_db()
        
            # Step 1: Get target districts (season-based)
            query = """
                SELECT DISTINCT d.id, d.state, d.district, d.language, 
                       d.latitude, d.longitude
                FROM districts d
                JOIN district_crops dc ON d.id = dc.district_id
                WHERE dc.season = ? AND d.is_active = 1
            """
            params = [season]
            if state:
                query += " AND d.state = ?"
                params.append(state)
            query += f" LIMIT {int(limit)}"
            
            districts = conn.execute(query, params).fetchall()
            conn.close()
            
            result = {
                'batch_id': batch_id,
                'season': season,
                'state_filter': state or 'All India',
                'total_districts': len(districts),
                'weather_fetched': 0,
                'at_risk': 0,
                'healthy': 0,
                'campaigns_created': 0,
                'errors': [],
                'campaigns': [],
                'district_health': [],
            }
            
            # Step 2: Fetch weather in PARALLEL (10 threads) for speed
            district_list = [dict(d) for d in districts]
            
            # Step 2+3: One district at a time — weather, then predict, then campaign
            for district in district_list:
                try:
                    weather = self.weather_service.fetch_and_cache(
                        district['id'], district['latitude'], district['longitude']
                    )
                    weather_summary = weather.get('summary')
                    weather_forecast = weather.get('forecast', [])  # daily breakdown
                    
                    if not weather_summary:
                        result['district_health'].append({
                            'district': district['district'],
                            'state': district['state'],
                            'status': 'no_data',
                            'status_label': 'Weather Unavailable',
                            'color': 'gray',
                        })
                        continue
                    
                    # Merge daily forecast into summary for DB storage
                    full_weather = {**weather_summary, 'forecast': weather_forecast}
                    
                    result['weather_fetched'] += 1
                    
                    # Predict diseases
                    predictions = self.predictor.predict_for_district(
                        district['id'], weather_summary, season=season
                    )
                    
                    latest = self._get_latest_prediction(district['id'])
                    
                    if not predictions or predictions[0]['probability'] < 0.2:
                        if latest and self._is_same_threat(latest, 'None', 'HEALTHY'):
                            result['healthy'] += 1
                            result['district_health'].append({
                                'district': district['district'],
                                'state': district['state'],
                                'status': 'healthy',
                                'status_label': 'Crop Healthy — No Change',
                                'color': 'green',
                                'weather': {
                                    'temp': weather_summary.get('avg_temp'),
                                    'humidity': weather_summary.get('avg_humidity'),
                                    'rainfall': weather_summary.get('total_rainfall'),
                                },
                                'unchanged': True,
                            })
                            continue
                        # HEALTHY — no significant disease risk
                        result['healthy'] += 1
                        
                        # Save healthy status to DB so Crop Overview can show it
                        try:
                            hconn = get_db()
                            hconn.execute("""
                                INSERT INTO predictions
                                (batch_id, district_id, crop, disease, probability, risk_level,
                                 weather_summary, prediction_method, product_recommended, product_dosage)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                batch_id, district['id'], 'N/A', 'None', 0.0, 'HEALTHY',
                                json.dumps(full_weather), 'pipeline', '', ''
                            ))
                            hconn.commit()
                            hconn.close()
                        except: pass
                        
                        result['district_health'].append({
                            'district': district['district'],
                            'state': district['state'],
                            'status': 'healthy',
                            'status_label': 'Crop Healthy — No Risk Detected',
                            'color': 'green',
                            'weather': {
                                'temp': weather_summary.get('avg_temp'),
                                'humidity': weather_summary.get('avg_humidity'),
                                'rainfall': weather_summary.get('total_rainfall'),
                            }
                        })
                    else:
                        # AT RISK — disease detected
                        top = predictions[0]
                        top['weather_summary'] = full_weather  # include daily forecast
                        
                        if latest and self._is_same_threat(latest, top['disease'], top['risk_level']):
                            result['at_risk'] += 1
                            health_entry = self._entry_from_stored(
                                district, latest, ' — No Change', syngenta_only=False
                            )
                            result['district_health'].append(health_entry)
                            if health_entry.get('campaign_id'):
                                pass  # existing campaign kept
                            elif not self._campaign_exists_for_disease(district['id'], top['disease']):
                                content = self.content_service.warm_pipeline_cache(top)
                                camp = self._create_campaign_for_prediction(
                                    batch_id, latest['id'], top, content
                                )
                                if camp:
                                    result['campaigns_created'] += 1
                                    health_entry['campaign_id'] = camp['id']
                            continue
                        
                        result['at_risk'] += 1
                        campaign = self._create_campaign(batch_id, top)
                        
                        health_entry = {
                            'district': district['district'],
                            'state': district['state'],
                            'status': 'at_risk',
                            'status_label': f'{top["disease"]} — {top["risk_level"]}',
                            'color': 'red' if top['risk_level'] == 'HIGH' else 'orange' if top['risk_level'] == 'MODERATE' else 'yellow',
                            'crop': top['crop'],
                            'disease': top['disease'],
                            'risk_level': top['risk_level'],
                            'probability': top['probability'],
                            'product': top['product'],
                            'weather': {
                                'temp': weather_summary.get('avg_temp'),
                                'humidity': weather_summary.get('avg_humidity'),
                                'rainfall': weather_summary.get('total_rainfall'),
                            }
                        }
                        result['district_health'].append(health_entry)
                        
                        if campaign:
                            result['campaigns_created'] += 1
                            health_entry['campaign_id'] = campaign['id']
                            result['campaigns'].append({
                                'id': campaign['id'],
                                'district': district['district'],
                                'state': district['state'],
                                'crop': top['crop'],
                                'disease': top['disease'],
                                'probability': top['probability'],
                                'risk_level': top['risk_level'],
                                'product': top['product'],
                                'sms': campaign['content'].get('sms', ''),
                            })
                        
                except Exception as e:
                    result['errors'].append(f"{district['district']}: {str(e)}")
            
            return result
        
        finally:
            CampaignService._pipeline_running = False
    
    def _get_syngenta_districts(self, conn, season, state=None):
        """All districts present in Syngenta growers data (case-insensitive match)."""
        query = """
            SELECT DISTINCT d.id, d.state, d.district, d.language,
                   d.latitude, d.longitude
            FROM districts d
            INNER JOIN (
                SELECT DISTINCT LOWER(TRIM(district)) AS district, LOWER(TRIM(state)) AS state
                FROM syngenta_growers
            ) sg ON LOWER(TRIM(d.district)) = sg.district AND LOWER(TRIM(d.state)) = sg.state
            JOIN district_crops dc ON d.id = dc.district_id
            WHERE dc.season = ? AND d.is_active = 1
        """
        params = [season]
        if state:
            query += " AND d.state = ?"
            params.append(state)
        query += " ORDER BY d.state, d.district"
        return conn.execute(query, params).fetchall()
    
    def _save_prediction_only(self, batch_id, prediction):
        """Persist prediction row (always — even if campaign is skipped)."""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO predictions 
            (batch_id, district_id, crop, disease, probability, risk_level,
             weather_summary, prediction_method, product_recommended, product_dosage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            batch_id, prediction['district_id'], prediction['crop'],
            prediction['disease'], prediction['probability'],
            prediction['risk_level'], json.dumps(prediction['weather_summary']),
            prediction.get('method', 'pipeline'), prediction['product'], prediction.get('dosage', '')
        ))
        prediction_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return prediction_id
    
    def _normalize_threat_key(self, disease, risk_level):
        """Unique key for district threat comparison (disease or healthy)."""
        if risk_level == 'HEALTHY' or not disease or disease in ('None', 'N/A'):
            return '__healthy__'
        return disease.strip().lower()
    
    def _get_latest_prediction(self, district_id):
        """Most recent prediction (+ linked campaign if any) for a district."""
        conn = get_db()
        row = conn.execute("""
            SELECT p.*, c.id as campaign_id
            FROM predictions p
            LEFT JOIN campaigns c ON c.prediction_id = p.id
            WHERE p.district_id = ?
            ORDER BY p.id DESC LIMIT 1
        """, (district_id,)).fetchone()
        conn.close()
        return dict(row) if row else None
    
    def _campaign_exists_for_disease(self, district_id, disease):
        """True if a campaign already exists for this district + disease."""
        if not disease or disease in ('None', 'N/A'):
            return False
        conn = get_db()
        row = conn.execute(
            "SELECT id FROM campaigns WHERE district_id = ? AND disease = ? LIMIT 1",
            (district_id, disease),
        ).fetchone()
        conn.close()
        return row is not None
    
    def _is_same_threat(self, latest, disease, risk_level):
        """Same disease/health status as the last stored prediction."""
        if not latest:
            return False
        return (
            self._normalize_threat_key(latest.get('disease'), latest.get('risk_level'))
            == self._normalize_threat_key(disease, risk_level)
        )
    
    def _entry_from_stored(self, district, row, label_suffix='', syngenta_only=False):
        """Build UI entry from an existing DB prediction row."""
        weather = {}
        try:
            if row.get('weather_summary'):
                w = json.loads(row['weather_summary']) if isinstance(row['weather_summary'], str) else row['weather_summary']
                weather = {
                    'temp': w.get('avg_temp'),
                    'humidity': w.get('avg_humidity'),
                    'rainfall': w.get('total_rainfall'),
                }
        except Exception:
            pass
        
        is_healthy = row.get('risk_level') == 'HEALTHY' or row.get('disease') in ('None', 'N/A')
        if is_healthy:
            entry = {
                'district': district['district'], 'state': district['state'],
                'status': 'healthy',
                'status_label': f'Crop Healthy{label_suffix}',
                'color': 'green', 'weather': weather, 'unchanged': True,
            }
        else:
            entry = {
                'district': district['district'], 'state': district['state'],
                'status': 'at_risk',
                'status_label': f'{row["disease"]} — {row["risk_level"]}{label_suffix}',
                'color': 'red' if row['risk_level'] == 'HIGH' else 'orange' if row['risk_level'] == 'MODERATE' else 'yellow',
                'crop': row['crop'], 'disease': row['disease'],
                'risk_level': row['risk_level'], 'probability': row['probability'],
                'product': row['product_recommended'],
                'weather': weather, 'unchanged': True,
            }
            if row.get('campaign_id'):
                entry['campaign_id'] = row['campaign_id']
        if syngenta_only:
            entry['syngenta'] = self._get_syngenta_enrichment(
                district['district'], district['state'], row.get('product_recommended'))
        return entry
    
    def _get_syngenta_enrichment(self, district_name, state, product_name=None):
        """Get Syngenta grower stats + inventory for a district"""
        try:
            conn = get_db()
            # Grower stats
            grower_stats = conn.execute("""
                SELECT COUNT(*) as cnt,
                       AVG(farm_size) as avg_farm,
                       SUM(CASE WHEN device_type='smartphone' THEN 1 ELSE 0 END) as smartphones,
                       SUM(CASE WHEN device_type='keypad' THEN 1 ELSE 0 END) as keypads,
                       AVG(grower_age) as avg_age
                FROM syngenta_growers WHERE district = ? AND state = ?
            """, (district_name, state)).fetchone()
            
            # Inventory availability (latest week, for recommended product)
            inventory = None
            if product_name:
                # Map our product name to SKU name via sku_product_map
                sku_row = conn.execute("""
                    SELECT sku_name FROM sku_product_map WHERE our_product = ?
                """, (product_name,)).fetchone()
                sku_name = sku_row['sku_name'] if sku_row else product_name
                
                inventory = conn.execute("""
                    SELECT SUM(si.sku_qty) as total_stock,
                           COUNT(DISTINCT si.retailer_id) as retailer_count
                    FROM syngenta_inventory si
                    JOIN syngenta_retailers sr ON si.retailer_id = sr.retailer_id
                    WHERE sr.district = ? AND sr.state = ? AND si.sku_name = ?
                    AND si.week_end_date = (
                        SELECT MAX(week_end_date) FROM syngenta_inventory
                    )
                """, (district_name, state, sku_name)).fetchone()
            
            # Retailer count in district
            ret_count = conn.execute("""
                SELECT COUNT(*) as cnt FROM syngenta_retailers
                WHERE district = ? AND state = ?
            """, (district_name, state)).fetchone()
            
            conn.close()
            
            result = {
                'grower_count': grower_stats['cnt'] if grower_stats else 0,
                'avg_farm_size': round(grower_stats['avg_farm'], 1) if grower_stats and grower_stats['avg_farm'] else 0,
                'smartphones': grower_stats['smartphones'] if grower_stats else 0,
                'keypads': grower_stats['keypads'] if grower_stats else 0,
                'avg_age': round(grower_stats['avg_age']) if grower_stats and grower_stats['avg_age'] else 0,
                'retailer_count': ret_count['cnt'] if ret_count else 0,
            }
            
            if inventory and inventory['total_stock']:
                result['product_stock'] = inventory['total_stock']
                result['stock_retailers'] = inventory['retailer_count']
            
            return result
        except Exception as e:
            return {'error': str(e)}

    def _get_standard_districts(self, conn, state=None, limit=1000):
        """Return active districts to scan. Season is used for prediction, not district selection."""
        query = """
            SELECT d.id, d.state, d.district, d.language, d.latitude, d.longitude
            FROM districts d
            WHERE d.is_active = 1
        """
        params = []
        if state:
            query += " AND d.state = ?"
            params.append(state)
        query += " ORDER BY d.state, d.district LIMIT ?"
        params.append(int(limit))
        return conn.execute(query, params).fetchall()

    def _analyze_district_for_pipeline(self, district, season):
        """Fetch weather and run ML prediction for one district in a worker thread."""
        weather = self.weather_service.fetch_and_cache(
            district['id'], district['latitude'], district['longitude']
        )
        weather_summary = weather.get('summary')
        weather_forecast = weather.get('forecast', [])
        latest = self._get_latest_prediction(district['id'])

        if not weather_summary:
            return {
                'district': district,
                'latest': latest,
                'weather_summary': None,
                'weather_forecast': [],
                'predictions': [],
                'cached': weather.get('cached', False),
            }

        predictions = self.predictor.predict_for_district(
            district['id'], weather_summary, season=season
        )
        return {
            'district': district,
            'latest': latest,
            'weather_summary': weather_summary,
            'weather_forecast': weather_forecast,
            'predictions': predictions or [],
            'cached': weather.get('cached', False),
        }
    
    def run_pipeline_stream(self, season=None, state=None, limit=1000, syngenta_only=False):
        """
        Streaming pipeline — yields each district result as SSE event.
        Sequential: one district at a time (weather -> predict -> campaign).
        syngenta_only: if True, run only on 33 Syngenta grower districts.
        """
        # Prevent duplicate runs
        if CampaignService._pipeline_running:
            yield {'type': 'error', 'message': 'Pipeline already running! Please wait.'}
            return
        
        CampaignService._pipeline_running = True
        
        try:
            if not season:
                season = self.predictor._get_current_season()
        
            batch_id = str(uuid.uuid4())[:8]
            conn = get_db()
            
            if syngenta_only:
                districts = self._get_syngenta_districts(conn, season, state)
            else:
                query = """
                    SELECT DISTINCT d.id, d.state, d.district, d.language,
                           d.latitude, d.longitude
                    FROM districts d
                    JOIN district_crops dc ON d.id = dc.district_id
                    WHERE dc.season = ? AND d.is_active = 1
                """
                params = [season]
                if state:
                    query += " AND d.state = ?"
                    params.append(state)
                query += f" LIMIT {int(limit)}"
                
                districts = conn.execute(query, params).fetchall()
        
            district_list = [dict(d) for d in districts]
            conn.close()
        
            total = len(district_list)
            healthy = 0
            at_risk = 0
            campaigns_created = 0
            skipped_unchanged = 0
            progress = 0
        
            yield {
                'type': 'init',
                'total': total,
                'season': season,
                'state_filter': state or 'All India',
                'batch_id': batch_id,
                'mode': 'syngenta_real' if syngenta_only else 'standard',
            }
        
            yield {'type': 'phase', 'phase': 'analyze',
                   'message': f'Processing {total} districts (no duplicate campaigns for same disease)...'}
            
            for district in district_list:
                progress += 1
                try:
                    yield {
                        'type': 'phase', 'phase': 'district',
                        'message': f'[{progress}/{total}] {district["district"]}, {district["state"]}...',
                    }
                    
                    latest = self._get_latest_prediction(district['id'])
                    
                    weather = self.weather_service.fetch_and_cache(
                        district['id'], district['latitude'], district['longitude']
                    )
                    weather_summary = weather.get('summary')
                    weather_forecast = weather.get('forecast', [])
                    
                    if not weather_summary:
                        yield {
                            'type': 'district', 'progress': progress, 'total': total,
                            'data': {
                                'district': district['district'], 'state': district['state'],
                                'status': 'no_data', 'status_label': 'Weather Unavailable', 'color': 'gray',
                            },
                        }
                        continue
                    
                    full_weather = {**weather_summary, 'forecast': weather_forecast}
                    predictions = self.predictor.predict_for_district(
                        district['id'], weather_summary, season=season
                    )
                    
                    if not predictions or predictions[0]['probability'] < 0.2:
                        if latest and self._is_same_threat(latest, 'None', 'HEALTHY'):
                            healthy += 1
                            skipped_unchanged += 1
                            entry = self._entry_from_stored(
                                district, latest, ' — No Change', syngenta_only
                            )
                            yield {'type': 'district', 'progress': progress, 'total': total, 'data': entry}
                            continue
                        healthy += 1
                        try:
                            hconn = get_db()
                            hconn.execute("""
                                INSERT INTO predictions
                                (batch_id, district_id, crop, disease, probability, risk_level,
                                 weather_summary, prediction_method, product_recommended, product_dosage)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (batch_id, district['id'], 'N/A', 'None', 0.0, 'HEALTHY',
                                  json.dumps(full_weather), 'pipeline', '', ''))
                            hconn.commit()
                            hconn.close()
                        except Exception:
                            pass
                        entry = {
                            'district': district['district'], 'state': district['state'],
                            'status': 'healthy', 'status_label': 'Crop Healthy -- No Risk Detected',
                            'color': 'green',
                            'weather': {
                                'temp': weather_summary.get('avg_temp'),
                                'humidity': weather_summary.get('avg_humidity'),
                                'rainfall': weather_summary.get('total_rainfall'),
                            },
                        }
                        if syngenta_only:
                            entry['syngenta'] = self._get_syngenta_enrichment(
                                district['district'], district['state'])
                        yield {'type': 'district', 'progress': progress, 'total': total, 'data': entry}
                        continue
                    
                    top = predictions[0]
                    top['weather_summary'] = full_weather
                    
                    if latest and self._is_same_threat(latest, top['disease'], top['risk_level']):
                        at_risk += 1
                        skipped_unchanged += 1
                        entry = self._entry_from_stored(
                            district, latest, ' — No Change', syngenta_only
                        )
                        if not entry.get('campaign_id') and not self._campaign_exists_for_disease(
                            district['id'], top['disease']
                        ):
                            content = self.content_service.warm_pipeline_cache(top)
                            camp = self._create_campaign_for_prediction(
                                batch_id, latest['id'], top, content
                            )
                            if camp:
                                campaigns_created += 1
                                entry['campaign_id'] = camp['id']
                        yield {'type': 'district', 'progress': progress, 'total': total, 'data': entry}
                        continue
                    
                    prediction_id = self._save_prediction_only(batch_id, top)
                    at_risk += 1
                    
                    campaign = None
                    if not self._campaign_exists_for_disease(district['id'], top['disease']):
                        content = self.content_service.warm_pipeline_cache(top)
                        campaign = self._create_campaign_for_prediction(
                            batch_id, prediction_id, top, content
                        )
                    
                    entry = {
                        'district': district['district'], 'state': district['state'],
                        'status': 'at_risk',
                        'status_label': f'{top["disease"]} -- {top["risk_level"]}',
                        'color': 'red' if top['risk_level'] == 'HIGH' else 'orange' if top['risk_level'] == 'MODERATE' else 'yellow',
                        'crop': top['crop'], 'disease': top['disease'],
                        'risk_level': top['risk_level'], 'probability': top['probability'],
                        'product': top['product'],
                        'weather': {
                            'temp': weather_summary.get('avg_temp'),
                            'humidity': weather_summary.get('avg_humidity'),
                            'rainfall': weather_summary.get('total_rainfall'),
                        },
                    }
                    if syngenta_only:
                        entry['syngenta'] = self._get_syngenta_enrichment(
                            district['district'], district['state'], top.get('product'))
                    if campaign:
                        campaigns_created += 1
                        entry['campaign_id'] = campaign['id']
                    yield {'type': 'district', 'progress': progress, 'total': total, 'data': entry}
                
                except Exception as e:
                    yield {
                        'type': 'district', 'progress': progress, 'total': total,
                        'data': {
                            'district': district['district'], 'state': district['state'],
                            'status': 'error', 'status_label': str(e), 'color': 'gray',
                        },
                    }
        
            # Final summary
            yield {
                'type': 'complete',
                'summary': {
                    'total': total, 'healthy': healthy, 'at_risk': at_risk,
                    'campaigns_created': campaigns_created,
                    'batch_id': batch_id, 'season': season,
                    'skipped_unchanged': skipped_unchanged,
                }
            }
        
        finally:
            CampaignService._pipeline_running = False
    
    def run_pipeline_stream_fast(self, season=None, state=None, limit=1000, syngenta_only=False):
        """
        Faster streaming pipeline. Weather and prediction run in parallel; DB campaign writes stay sequential.
        """
        if CampaignService._pipeline_running:
            yield {'type': 'error', 'message': 'Pipeline already running! Please wait.'}
            return

        CampaignService._pipeline_running = True

        try:
            if not season:
                season = self.predictor._get_current_season()

            batch_id = str(uuid.uuid4())[:8]
            conn = get_db()
            if syngenta_only:
                districts = self._get_syngenta_districts(conn, season, state)
            else:
                districts = self._get_standard_districts(conn, state, limit)
            district_list = [dict(d) for d in districts]
            conn.close()

            total = len(district_list)
            healthy = 0
            at_risk = 0
            campaigns_created = 0
            skipped_unchanged = 0
            weather_cached = 0
            weather_fetched = 0
            progress = 0
            max_workers = max(1, min(Config.PIPELINE_MAX_WORKERS, total or 1))

            yield {
                'type': 'init',
                'total': total,
                'season': season,
                'state_filter': state or 'All India',
                'batch_id': batch_id,
                'mode': 'syngenta_real' if syngenta_only else 'standard',
                'workers': max_workers,
            }
            yield {
                'type': 'phase',
                'phase': 'analyze',
                'message': f'Processing {total} districts with {max_workers} parallel workers...',
            }

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(self._analyze_district_for_pipeline, district, season): district
                    for district in district_list
                }

                for future in concurrent.futures.as_completed(future_map):
                    progress += 1
                    district = future_map[future]
                    try:
                        analysis = future.result()
                        district = analysis['district']
                        latest = analysis['latest']
                        weather_summary = analysis['weather_summary']
                        weather_forecast = analysis['weather_forecast']
                        predictions = analysis['predictions']
                        if analysis.get('cached'):
                            weather_cached += 1
                        else:
                            weather_fetched += 1
                    except Exception as e:
                        yield {
                            'type': 'district', 'progress': progress, 'total': total,
                            'data': {
                                'district': district['district'],
                                'state': district['state'],
                                'status': 'error',
                                'status_label': str(e),
                                'color': 'gray',
                            },
                        }
                        continue

                    yield {
                        'type': 'phase',
                        'phase': 'district',
                        'message': f'[{progress}/{total}] {district["district"]}, {district["state"]}...',
                    }

                    if not weather_summary:
                        yield {
                            'type': 'district', 'progress': progress, 'total': total,
                            'data': {
                                'district': district['district'],
                                'state': district['state'],
                                'status': 'no_data',
                                'status_label': 'Weather Unavailable',
                                'color': 'gray',
                            },
                        }
                        continue

                    full_weather = {**weather_summary, 'forecast': weather_forecast}

                    if not predictions or predictions[0]['probability'] < 0.2:
                        if latest and self._is_same_threat(latest, 'None', 'HEALTHY'):
                            healthy += 1
                            skipped_unchanged += 1
                            entry = self._entry_from_stored(district, latest, ' -- No Change', syngenta_only)
                            yield {'type': 'district', 'progress': progress, 'total': total, 'data': entry}
                            continue

                        healthy += 1
                        try:
                            hconn = get_db()
                            hconn.execute("""
                                INSERT INTO predictions
                                (batch_id, district_id, crop, disease, probability, risk_level,
                                 weather_summary, prediction_method, product_recommended, product_dosage)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                batch_id, district['id'], 'N/A', 'None', 0.0, 'HEALTHY',
                                json.dumps(full_weather), 'pipeline', '', ''
                            ))
                            hconn.commit()
                            hconn.close()
                        except Exception:
                            pass

                        entry = {
                            'district': district['district'],
                            'state': district['state'],
                            'status': 'healthy',
                            'status_label': 'Crop Healthy -- No Risk Detected',
                            'color': 'green',
                            'weather': {
                                'temp': weather_summary.get('avg_temp'),
                                'humidity': weather_summary.get('avg_humidity'),
                                'rainfall': weather_summary.get('total_rainfall'),
                            },
                        }
                        if syngenta_only:
                            entry['syngenta'] = self._get_syngenta_enrichment(
                                district['district'], district['state'])
                        yield {'type': 'district', 'progress': progress, 'total': total, 'data': entry}
                        continue

                    top = predictions[0]
                    top['weather_summary'] = full_weather

                    if latest and self._is_same_threat(latest, top['disease'], top['risk_level']):
                        at_risk += 1
                        skipped_unchanged += 1
                        entry = self._entry_from_stored(district, latest, ' -- No Change', syngenta_only)
                        if not entry.get('campaign_id') and not self._campaign_exists_for_disease(
                            district['id'], top['disease']
                        ):
                            content = self.content_service.warm_pipeline_cache(top)
                            camp = self._create_campaign_for_prediction(batch_id, latest['id'], top, content)
                            if camp:
                                campaigns_created += 1
                                entry['campaign_id'] = camp['id']
                        yield {'type': 'district', 'progress': progress, 'total': total, 'data': entry}
                        continue

                    prediction_id = self._save_prediction_only(batch_id, top)
                    at_risk += 1

                    campaign = None
                    if not self._campaign_exists_for_disease(district['id'], top['disease']):
                        content = self.content_service.warm_pipeline_cache(top)
                        campaign = self._create_campaign_for_prediction(batch_id, prediction_id, top, content)

                    entry = {
                        'district': district['district'],
                        'state': district['state'],
                        'status': 'at_risk',
                        'status_label': f'{top["disease"]} -- {top["risk_level"]}',
                        'color': 'red' if top['risk_level'] == 'HIGH' else 'orange' if top['risk_level'] == 'MODERATE' else 'yellow',
                        'crop': top['crop'],
                        'disease': top['disease'],
                        'risk_level': top['risk_level'],
                        'probability': top['probability'],
                        'product': top['product'],
                        'weather': {
                            'temp': weather_summary.get('avg_temp'),
                            'humidity': weather_summary.get('avg_humidity'),
                            'rainfall': weather_summary.get('total_rainfall'),
                        },
                    }
                    if syngenta_only:
                        entry['syngenta'] = self._get_syngenta_enrichment(
                            district['district'], district['state'], top.get('product'))
                    if campaign:
                        campaigns_created += 1
                        entry['campaign_id'] = campaign['id']
                    yield {'type': 'district', 'progress': progress, 'total': total, 'data': entry}

            yield {
                'type': 'complete',
                'summary': {
                    'total': total,
                    'healthy': healthy,
                    'at_risk': at_risk,
                    'campaigns_created': campaigns_created,
                    'batch_id': batch_id,
                    'season': season,
                    'skipped_unchanged': skipped_unchanged,
                    'weather_cached': weather_cached,
                    'weather_fetched': weather_fetched,
                    'workers': max_workers,
                },
            }
        finally:
            CampaignService._pipeline_running = False

    def _create_campaign(self, batch_id, prediction):
        """Create a campaign record with generated content"""
        
        # Check cooldown
        if not self._can_send(prediction['district_id'], prediction['disease']):
            return None
        
        # Use pipeline cache when available; otherwise generate (Gemini or template)
        content = self.content_service.get_cached_content(prediction)
        if not content:
            content = self.content_service.generate_campaign_content(prediction)
        
        conn = get_db()
        cursor = conn.cursor()
        
        # Save prediction first
        cursor.execute("""
            INSERT INTO predictions 
            (batch_id, district_id, crop, disease, probability, risk_level,
             weather_summary, prediction_method, product_recommended, product_dosage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            batch_id, prediction['district_id'], prediction['crop'],
            prediction['disease'], prediction['probability'],
            prediction['risk_level'], json.dumps(prediction['weather_summary']),
            prediction['method'], prediction['product'], prediction['dosage']
        ))
        prediction_id = cursor.lastrowid
        
        # Save campaign
        cursor.execute("""
            INSERT INTO campaigns 
            (batch_id, prediction_id, district_id, campaign_type, status,
             risk_level, crop, disease, product, language,
             message_whatsapp, message_sms, message_voice_script,
             poster_headline, poster_body, approved_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            batch_id, prediction_id, prediction['district_id'],
            'disease_alert', 'pending',
            prediction['risk_level'], prediction['crop'],
            prediction['disease'], prediction['product'],
            prediction['language'],
            content.get('whatsapp', ''), content.get('sms', ''),
            content.get('voice_script', ''),
            content.get('poster_headline', ''), content.get('poster_body', ''),
            'system'
        ))
        campaign_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return {'id': campaign_id, 'content': content}
    
    def _create_campaign_for_prediction(self, batch_id, prediction_id, prediction, content):
        """Attach campaign to an existing prediction (prediction always kept in DB)."""
        if not self._can_send(prediction['district_id'], prediction['disease']):
            return None
        
        if not content:
            content = self.content_service._template_generate(prediction)
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO campaigns 
            (batch_id, prediction_id, district_id, campaign_type, status,
             risk_level, crop, disease, product, language,
             message_whatsapp, message_sms, message_voice_script,
             poster_headline, poster_body, approved_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            batch_id, prediction_id, prediction['district_id'],
            'disease_alert', 'pending',
            prediction['risk_level'], prediction['crop'],
            prediction['disease'], prediction['product'],
            prediction['language'],
            content.get('whatsapp', ''), content.get('sms', ''),
            content.get('voice_script', ''),
            content.get('poster_headline', ''), content.get('poster_body', ''),
            'system'
        ))
        campaign_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return {'id': campaign_id, 'content': content}
    
    def _create_campaign_with_content(self, batch_id, prediction, content):
        """Create prediction + campaign (legacy path)."""
        prediction_id = self._save_prediction_only(batch_id, prediction)
        return self._create_campaign_for_prediction(batch_id, prediction_id, prediction, content)
    
    def _can_send(self, district_id, disease):
        """Block duplicate campaigns for the same district + disease."""
        if not disease or disease in ('None', 'N/A'):
            return False
        
        if self._campaign_exists_for_disease(district_id, disease):
            return False
        
        if Config.DISEASE_REPEAT_DAYS > 0:
            conn = get_db()
            recent = conn.execute("""
                SELECT created_at FROM campaigns 
                WHERE district_id = ? AND disease != ?
                ORDER BY created_at DESC LIMIT 1
            """, (district_id, disease)).fetchone()
            conn.close()
            if recent:
                last_time = datetime.fromisoformat(recent['created_at'])
                if datetime.now() - last_time < timedelta(days=Config.DISEASE_REPEAT_DAYS):
                    return False
        
        return True
    
    def approve_campaigns(self, campaign_ids=None, state=None, auto=False, send_now=False):
        """Approve pending campaigns and optionally send to test numbers"""
        conn = get_db()
        
        if auto:
            conn.execute("""
                UPDATE campaigns SET status='approved', 
                approved_at=CURRENT_TIMESTAMP, approved_by='auto'
                WHERE status='pending'
            """)
        elif state:
            conn.execute("""
                UPDATE campaigns SET status='approved',
                approved_at=CURRENT_TIMESTAMP, approved_by='admin'
                WHERE status='pending' AND district_id IN (
                    SELECT id FROM districts WHERE state=?
                )
            """, (state,))
        elif campaign_ids:
            placeholders = ','.join('?' * len(campaign_ids))
            conn.execute(f"""
                UPDATE campaigns SET status='approved',
                approved_at=CURRENT_TIMESTAMP, approved_by='admin'
                WHERE id IN ({placeholders})
            """, campaign_ids)
        
        count = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        
        # Auto-deliver to test numbers if requested
        delivery_results = []
        if send_now and count > 0:
            approved = conn.execute("""
                SELECT id FROM campaigns WHERE status='approved'
                ORDER BY approved_at DESC LIMIT ?
            """, (count,)).fetchall()
            conn.close()
            
            from services.delivery_service import DeliveryService
            delivery = DeliveryService()
            for c in approved:
                r = delivery.send_campaign_to_test_numbers(c['id'])
                delivery_results.append({
                    'campaign_id': c['id'],
                    'sent': r.get('numbers_sent', 0)
                })
        else:
            conn.close()
        
        return {'approved': count, 'delivered': delivery_results}
    
    def get_campaigns(self, status=None, state=None, batch_id=None, limit=50):
        """Get campaigns with filters"""
        conn = get_db()
        
        query = """
            SELECT c.*, d.state, d.district as district_name, d.language
            FROM campaigns c
            JOIN districts d ON c.district_id = d.id
            WHERE 1=1
        """
        params = []
        
        if status:
            query += " AND c.status = ?"
            params.append(status)
        if state:
            query += " AND d.state = ?"
            params.append(state)
        if batch_id:
            query += " AND c.batch_id = ?"
            params.append(batch_id)
        
        query += f" ORDER BY c.created_at DESC LIMIT {int(limit)}"
        
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    
    def run_demo_pipeline(self, state=None, limit=50):
        """
        Demo pipeline with simulated monsoon weather.
        Logic: Season → ALL districts with that season's crops → ML predict → Campaign
        If state=None, runs for ALL India. If state given, filters to that state.
        """
        batch_id = 'demo-' + str(uuid.uuid4())[:6]
        season = 'Kharif'  # Demo uses Kharif (monsoon) for maximum impact
        conn = get_db()
        
        # Get ALL districts that have crops for this season
        query = """
            SELECT DISTINCT d.id, d.state, d.district, d.language, 
                   d.latitude, d.longitude
            FROM districts d
            JOIN district_crops dc ON d.id = dc.district_id
            WHERE dc.season = ? AND d.is_active = 1
        """
        params = [season]
        
        if state:
            query += " AND d.state = ?"
            params.append(state)
        
        query += f" LIMIT {int(limit)}"
        
        districts = conn.execute(query, params).fetchall()
        conn.close()
        
        # Simulated monsoon weather (realistic for demo)
        demo_weather = {
            'avg_temp': 28.5,
            'max_temp': 33.0,
            'min_temp': 24.0,
            'avg_humidity': 85.0,
            'total_rainfall': 120.0,
            'rainy_days': 5,
            'consecutive_wet_days': 4,
            'avg_wind_speed': 12.0,
        }
        
        result = {
            'batch_id': batch_id,
            'mode': 'demo',
            'season': season,
            'state_filter': state or 'All India',
            'total_districts': len(districts),
            'weather_simulated': demo_weather,
            'campaigns': [],
            'total': 0,
        }
        
        for d in districts:
            district = dict(d)
            predictions = self.predictor.predict_for_district(
                district['id'], demo_weather, season=season
            )
            
            if predictions:
                top = predictions[0]
                campaign = self._create_campaign(batch_id, top)
                if campaign:
                    result['campaigns'].append({
                        'id': campaign['id'],
                        'district': district['district'],
                        'state': district['state'],
                        'crop': top['crop'],
                        'disease': top['disease'],
                        'probability': top['probability'],
                        'risk_level': top['risk_level'],
                        'product': top['product'],
                        'sms': campaign['content'].get('sms', ''),
                    })
                    result['total'] += 1
        
        return result

