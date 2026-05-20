# KrishiConnect AI - Inventory Intelligence Service
# Supply chain analysis: demand forecast vs retailer stock
import time
from database import get_db


class InventoryService:
    """Analyze Syngenta retailer inventory vs disease predictions"""
    
    # Simple in-memory cache (resets on server restart)
    _cache = {}
    _CACHE_TTL = 300  # 5 minutes
    
    def get_supply_chain_alerts(self):
        """
        Cross-reference ML predictions with retailer inventory.
        Returns districts where predicted product demand > available stock.
        """
        # Check cache first
        if 'supply_alerts' in self._cache:
            entry = self._cache['supply_alerts']
            if (time.time() - entry['ts']) < self._CACHE_TTL:
                return entry['data']
        
        conn = get_db()
        
        # Get latest predictions that have campaigns
        predictions = conn.execute("""
            SELECT p.district_id, d.district, d.state, p.disease, p.risk_level,
                   p.product_recommended, p.probability,
                   c.status as campaign_status
            FROM predictions p
            JOIN districts d ON p.district_id = d.id
            LEFT JOIN campaigns c ON c.prediction_id = p.id
            WHERE p.risk_level IN ('HIGH', 'MODERATE')
            AND p.id IN (SELECT MAX(id) FROM predictions GROUP BY district_id, disease)
            ORDER BY p.risk_level DESC, p.probability DESC
        """).fetchall()
        
        alerts = []
        for pred in predictions:
            p = dict(pred)
            product = p['product_recommended'] or ''
            
            # Find matching SKU
            sku_row = None
            if product and product.strip():
                first_word = product.split()[0] if product.split() else product
                sku_row = conn.execute("""
                    SELECT sku_name FROM sku_product_map 
                    WHERE our_product LIKE ? OR our_product LIKE ?
                    LIMIT 1
                """, (product, first_word + '%')).fetchone()
            
            sku_name = sku_row['sku_name'] if sku_row else None
            
            # Get retailer stock for this district + product
            stock_info = self._get_district_stock(conn, p['state'], p['district'], sku_name)
            
            # Get grower count for demand estimate
            grower_count = self._get_grower_count(conn, p['state'], p['district'])
            
            # Calculate demand
            severity_multiplier = 0.7 if p['risk_level'] == 'HIGH' else 0.4
            units_per_farmer = 2
            estimated_demand = int(grower_count * severity_multiplier * units_per_farmer)
            
            deficit = estimated_demand - stock_info['total_stock']
            
            if deficit > 0:
                status = 'URGENT' if deficit > estimated_demand * 0.5 else 'RESTOCK'
            elif stock_info['total_stock'] == 0 and stock_info['has_retailers']:
                status = 'OUT_OF_STOCK'
            elif not stock_info['has_retailers']:
                status = 'NO_COVERAGE'
            else:
                status = 'OK'
            
            alerts.append({
                'district': p['district'],
                'state': p['state'],
                'disease': p['disease'],
                'risk_level': p['risk_level'],
                'product': product,
                'sku_name': sku_name,
                'estimated_demand': estimated_demand,
                'grower_count': grower_count,
                'total_stock': stock_info['total_stock'],
                'retailer_count': stock_info['retailer_count'],
                'retailers_with_stock': stock_info['retailers_with_stock'],
                'top_retailers': stock_info['top_retailers'],
                'avg_weekly_sales': stock_info['avg_weekly_sales'],
                'weeks_of_stock': round(stock_info['total_stock'] / stock_info['avg_weekly_sales'], 1) if stock_info['avg_weekly_sales'] > 0 else 0,
                'deficit': max(0, deficit),
                'status': status,
            })
        
        conn.close()
        
        # Sort: URGENT first, then RESTOCK, then rest
        priority = {'URGENT': 0, 'OUT_OF_STOCK': 1, 'RESTOCK': 2, 'NO_COVERAGE': 3, 'OK': 4}
        alerts.sort(key=lambda a: (priority.get(a['status'], 5), -a['deficit']))
        
        # Cache result
        InventoryService._cache['supply_alerts'] = {'data': alerts, 'ts': time.time()}
        return alerts
    
    def _get_district_stock(self, conn, state, district, sku_name):
        """Get current stock for a product in a district's retailers"""
        result = {
            'total_stock': 0,
            'retailer_count': 0,
            'retailers_with_stock': 0,
            'has_retailers': False,
            'top_retailers': [],
            'avg_weekly_sales': 0,
        }
        
        # Get retailers in this district
        retailers = conn.execute("""
            SELECT retailer_id FROM syngenta_retailers 
            WHERE state = ? AND district = ?
        """, (state, district)).fetchall()
        
        if not retailers:
            return result
        
        result['has_retailers'] = True
        result['retailer_count'] = len(retailers)
        ret_ids = [r['retailer_id'] for r in retailers]
        
        if not sku_name:
            return result
        
        # Get latest week stock for each retailer
        placeholders = ','.join('?' * len(ret_ids))
        stocks = conn.execute(f"""
            SELECT i.retailer_id, i.sku_qty
            FROM syngenta_inventory i
            WHERE i.retailer_id IN ({placeholders})
            AND i.sku_name = ?
            AND i.week_end_date = (SELECT MAX(week_end_date) FROM syngenta_inventory)
            ORDER BY i.sku_qty DESC
        """, ret_ids + [sku_name]).fetchall()
        
        for s in stocks:
            result['total_stock'] += s['sku_qty']
            if s['sku_qty'] > 0:
                result['retailers_with_stock'] += 1
                if len(result['top_retailers']) < 3:
                    result['top_retailers'].append({
                        'retailer_id': s['retailer_id'],
                        'qty': s['sku_qty'],
                    })
        
        # Average weekly sales from POS
        sales = conn.execute(f"""
            SELECT COALESCE(AVG(weekly_total), 0) as avg_sales FROM (
                SELECT SUM(sku_qty) as weekly_total
                FROM syngenta_pos
                WHERE retailer_id IN ({placeholders})
                AND sku_name = ?
                GROUP BY strftime('%Y-%W', transaction_date)
            )
        """, ret_ids + [sku_name]).fetchone()
        
        result['avg_weekly_sales'] = round(sales['avg_sales'], 1) if sales else 0
        
        return result
    
    def _get_grower_count(self, conn, state, district):
        """Get farmer count in district"""
        row = conn.execute("""
            SELECT COUNT(*) as cnt FROM syngenta_growers
            WHERE state = ? AND district = ?
        """, (state, district)).fetchone()
        
        if row and row['cnt'] > 0:
            return row['cnt']
        
        # Estimate for districts without grower data: use average
        avg = conn.execute("SELECT COUNT(*) / COUNT(DISTINCT district) as avg FROM syngenta_growers").fetchone()
        return int(avg['avg']) if avg else 100
    
    def get_overview_stats(self):
        """Dashboard overview stats (cached 5 min)"""
        if 'overview' in self._cache:
            entry = self._cache['overview']
            if (time.time() - entry['ts']) < self._CACHE_TTL:
                return entry['data']
        
        conn = get_db()
        
        stats = {}
        
        # Total retailers & districts covered
        r = conn.execute("SELECT COUNT(*) as cnt, COUNT(DISTINCT district) as districts FROM syngenta_retailers").fetchone()
        stats['total_retailers'] = r['cnt']
        stats['covered_districts'] = r['districts']
        
        # Total SKUs
        stats['total_skus'] = conn.execute("SELECT COUNT(DISTINCT sku_name) FROM syngenta_inventory").fetchone()[0]
        
        # Latest week stock summary
        latest = conn.execute("SELECT MAX(week_end_date) as w FROM syngenta_inventory").fetchone()['w']
        stats['latest_week'] = latest
        
        stock_summary = conn.execute("""
            SELECT sku_name,
                SUM(sku_qty) as total_qty,
                COUNT(DISTINCT retailer_id) as retailers,
                SUM(CASE WHEN sku_qty > 0 THEN 1 ELSE 0 END) as in_stock_retailers
            FROM syngenta_inventory
            WHERE week_end_date = ?
            GROUP BY sku_name
            ORDER BY total_qty DESC
        """, (latest,)).fetchall()
        stats['stock_by_product'] = [dict(s) for s in stock_summary]
        
        # Grower stats
        gr = conn.execute("""
            SELECT COUNT(*) as total,
                SUM(CASE WHEN device_type='smartphone' THEN 1 ELSE 0 END) as smartphone,
                SUM(CASE WHEN device_type='keypad' THEN 1 ELSE 0 END) as keypad,
                SUM(CASE WHEN device_type='unknown' THEN 1 ELSE 0 END) as unknown
            FROM syngenta_growers
        """).fetchone()
        stats['growers'] = dict(gr)
        
        conn.close()
        InventoryService._cache['overview'] = {'data': stats, 'ts': time.time()}
        return stats
    
    def get_channel_routing(self):
        """
        Channel Routing Intelligence — state-wise device breakdown.
        Maps device_type → recommended delivery channels.
        """
        conn = get_db()
        
        # State-wise breakdown
        rows = conn.execute("""
            SELECT state, district,
                COUNT(*) as total,
                SUM(CASE WHEN device_type='smartphone' THEN 1 ELSE 0 END) as smartphone,
                SUM(CASE WHEN device_type='keypad' THEN 1 ELSE 0 END) as keypad,
                SUM(CASE WHEN device_type='unknown' THEN 1 ELSE 0 END) as unknown,
                ROUND(AVG(farm_size), 1) as avg_farm,
                ROUND(AVG(grower_age)) as avg_age
            FROM syngenta_growers
            GROUP BY state, district
            ORDER BY state, district
        """).fetchall()
        
        # Aggregate totals
        totals = conn.execute("""
            SELECT COUNT(*) as total,
                SUM(CASE WHEN device_type='smartphone' THEN 1 ELSE 0 END) as smartphone,
                SUM(CASE WHEN device_type='keypad' THEN 1 ELSE 0 END) as keypad,
                SUM(CASE WHEN device_type='unknown' THEN 1 ELSE 0 END) as unknown
            FROM syngenta_growers
        """).fetchone()
        
        conn.close()
        
        districts = []
        for r in rows:
            d = dict(r)
            total = d['total'] or 1
            d['smartphone_pct'] = round(d['smartphone'] / total * 100, 1)
            d['keypad_pct'] = round(d['keypad'] / total * 100, 1)
            d['unknown_pct'] = round(d['unknown'] / total * 100, 1)
            # Channel routing
            d['channels'] = {
                'whatsapp_text': d['smartphone'],
                'whatsapp_poster': d['smartphone'],
                'voice_call': d['keypad'] + d['unknown'],
                'sms_only': d['keypad'] + d['unknown'],
            }
            districts.append(d)
        
        t = dict(totals)
        total_all = t['total'] or 1
        
        return {
            'districts': districts,
            'totals': {
                'total': t['total'],
                'smartphone': t['smartphone'],
                'keypad': t['keypad'],
                'unknown': t['unknown'],
                'smartphone_pct': round(t['smartphone'] / total_all * 100, 1),
                'keypad_pct': round(t['keypad'] / total_all * 100, 1),
                'unknown_pct': round(t['unknown'] / total_all * 100, 1),
            },
            'channel_plan': {
                'whatsapp_eligible': t['smartphone'],
                'voice_eligible': t['keypad'] + t['unknown'],
                'sms_fallback': t['keypad'] + t['unknown'],
                'poster_eligible': t['smartphone'],
                'total_reach': t['total'],
            },
        }

    def clear_cache(self):
        """Clear all cached data (call after pipeline run or system reset)"""
        InventoryService._cache.clear()
