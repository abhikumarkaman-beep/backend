# KrishiConnect AI - Delivery Service (Twilio WhatsApp — Active Account System)
# Sends 3 separate messages: Text advisory, Voice audio, Poster image
import os
import time
from datetime import datetime
from config import Config
from database import get_db
from services.media_service import MediaService


class DeliveryService:
    """Send campaign messages via WhatsApp — single active account, manual switch on limit"""
    
    def __init__(self):
        self.clients = []
        self.active_index = 0
        self.whatsapp_from = Config.TWILIO_WHATSAPP_NUMBER
        self.test_numbers = [n.strip() for n in os.getenv('TEST_NUMBERS', '').split(',') if n.strip()]
        self.accounts = Config.TWILIO_ACCOUNTS
        self.media = MediaService()
        self._init_twilio()
    
    def _init_twilio(self):
        """Initialize ALL Twilio accounts"""
        if not self.accounts:
            print("[DELIVERY] WARNING: No Twilio accounts configured.")
            return
        
        try:
            from twilio.rest import Client
            for i, acc in enumerate(self.accounts):
                try:
                    client = Client(acc['sid'], acc['token'])
                    self.clients.append({
                        'client': client,
                        'sid': acc['sid'],
                        'sandbox_code': acc.get('sandbox_code', ''),
                        'limited': False,
                    })
                    print(f"[DELIVERY] Account #{i+1} ready ({acc['sid'][:10]}...) Code: {acc.get('sandbox_code', 'N/A')}")
                except Exception as e:
                    print(f"[DELIVERY] Account #{i+1} FAILED: {e}")
            
            print(f"[DELIVERY] {len(self.clients)} account(s) loaded. Active: #{self.active_index + 1}")
            print(f"[DELIVERY] Test numbers: {self.test_numbers}")
        except ImportError:
            print("[DELIVERY] ERROR: twilio not installed. Run: pip install twilio")
    
    def get_active_info(self):
        """Return active account's sandbox code — frontend shows this"""
        if not self.clients:
            return {
                'active_account': 0,
                'total_accounts': 0,
                'sandbox_code': '',
                'whatsapp_number': '+14155238886',
                'status': 'no_accounts',
                'message': 'No Twilio accounts configured',
            }
        
        acc = self.clients[self.active_index]
        return {
            'active_account': self.active_index + 1,
            'total_accounts': len(self.clients),
            'sandbox_code': acc['sandbox_code'],
            'whatsapp_number': '+14155238886',
            'status': 'limited' if acc['limited'] else 'ready',
            'message': f"Account #{self.active_index + 1} active. Join: {acc['sandbox_code']}",
        }
    
    def switch_account(self):
        """Manually switch to next available account"""
        if len(self.clients) <= 1:
            return {'error': 'Only 1 account configured', 'switched': False}
        
        old = self.active_index
        self.active_index = (self.active_index + 1) % len(self.clients)
        self.clients[self.active_index]['limited'] = False
        
        new_code = self.clients[self.active_index]['sandbox_code']
        print(f"[DELIVERY] Switched from Account #{old + 1} to #{self.active_index + 1}")
        
        return {
            'switched': True,
            'old_account': old + 1,
            'new_account': self.active_index + 1,
            'sandbox_code': new_code,
            'message': f"Switched to Account #{self.active_index + 1}. Join: {new_code}",
        }
    
    def send_whatsapp(self, to_number, message, campaign_id=None):
        """Send WhatsApp from ACTIVE account only (no auto-rotation)"""
        if not self.clients:
            return {'status': 'error', 'error': 'No Twilio accounts configured', 'channel': 'whatsapp'}
        
        client = self.clients[self.active_index]['client']
        clean = to_number.replace('whatsapp:', '').replace('+91', '').replace('+', '').strip()
        to_formatted = f"whatsapp:+91{clean}"
        
        try:
            msg = client.messages.create(
                from_=self.whatsapp_from,
                to=to_formatted,
                body=message,
            )
            
            if campaign_id:
                self._log_delivery(campaign_id, 'whatsapp', msg.sid, phone=to_formatted)
            
            print(f"[DELIVERY] SENT to {to_formatted} | Account #{self.active_index + 1} | SID: {msg.sid}")
            return {
                'status': 'sent',
                'sid': msg.sid,
                'to': to_formatted,
                'channel': 'whatsapp',
                'account': self.active_index + 1,
            }
        except Exception as e:
            error_msg = str(e)
            
            # Mark as limited if 429
            if '429' in error_msg or 'exceeded' in error_msg.lower():
                self.clients[self.active_index]['limited'] = True
                print(f"[DELIVERY] Account #{self.active_index + 1} LIMIT HIT!")
                
                # Find next available
                next_available = None
                for i in range(len(self.clients)):
                    idx = (self.active_index + 1 + i) % len(self.clients)
                    if not self.clients[idx]['limited']:
                        next_available = idx
                        break
                
                switch_msg = ""
                if next_available is not None:
                    next_code = self.clients[next_available]['sandbox_code']
                    switch_msg = f" Switch to Account #{next_available + 1} ({next_code})"
                
                return {
                    'status': 'limited',
                    'error': f'Daily limit hit on Account #{self.active_index + 1}.{switch_msg}',
                    'channel': 'whatsapp',
                    'needs_switch': True,
                    'next_account': next_available + 1 if next_available is not None else None,
                    'next_sandbox_code': self.clients[next_available]['sandbox_code'] if next_available is not None else None,
                }
            
            if campaign_id:
                self._log_delivery(campaign_id, 'whatsapp', None, error_msg, phone=to_formatted)
            print(f"[DELIVERY] FAILED to {to_formatted}: {error_msg}")
            return {'status': 'error', 'error': error_msg, 'channel': 'whatsapp'}
    
    def _build_merged_message(self, campaign):
        """Merge all 4 channels into 1 WhatsApp message"""
        parts = []
        
        parts.append("*KrishiConnect AI - Campaign Alert*")
        district = campaign.get('district_name', 'N/A')
        state = campaign.get('state', '')
        parts.append(f"District: *{district}* ({state})")
        parts.append("")
        
        sms = campaign.get('message_sms', '')
        if sms:
            parts.append("--- *SMS ALERT* ---")
            parts.append(sms)
            parts.append("")
        
        wa = campaign.get('message_whatsapp', '')
        if wa:
            parts.append("--- *WHATSAPP ADVISORY* ---")
            parts.append(wa)
            parts.append("")
        
        voice = campaign.get('message_voice_script', '')
        if voice:
            parts.append("--- *VOICE CALL SCRIPT* ---")
            parts.append(f'_{voice}_')
            parts.append("")
        
        headline = campaign.get('poster_headline', '')
        body = campaign.get('poster_body', '')
        if headline or body:
            parts.append("--- *POSTER / WALL WRITING* ---")
            if headline:
                parts.append(f"*{headline}*")
            if body:
                parts.append(body)
            parts.append("")
        
        parts.append("_Powered by KrishiConnect AI x Syngenta India_")
        
        return "\n".join(parts)
    
    def send_whatsapp_media(self, to_number, media_url, caption='', campaign_id=None):
        """Send media (audio/image) via WhatsApp"""
        if not self.clients:
            return {'status': 'error', 'error': 'No Twilio accounts configured'}
        
        client = self.clients[self.active_index]['client']
        clean = to_number.replace('whatsapp:', '').replace('+91', '').replace('+', '').strip()
        to_formatted = f"whatsapp:+91{clean}"
        
        try:
            kwargs = {
                'from_': self.whatsapp_from,
                'to': to_formatted,
                'media_url': [media_url],
            }
            if caption:
                kwargs['body'] = caption
            
            msg = client.messages.create(**kwargs)
            if campaign_id:
                self._log_delivery(campaign_id, 'whatsapp_media', msg.sid, phone=to_formatted)
            print(f"[DELIVERY] MEDIA SENT to {to_formatted} | SID: {msg.sid}")
            return {'status': 'sent', 'sid': msg.sid, 'to': to_formatted}
        except Exception as e:
            error_msg = str(e)
            if '429' in error_msg or 'exceeded' in error_msg.lower():
                self.clients[self.active_index]['limited'] = True
                return {'status': 'limited', 'error': error_msg, 'needs_switch': True}
            print(f"[DELIVERY] MEDIA FAILED: {error_msg}")
            return {'status': 'error', 'error': error_msg}
    
    def send_campaign_to_test_numbers(self, campaign_id):
        """
        Send campaign as 3 SEPARATE WhatsApp messages:
        1. Text advisory (SMS + WhatsApp content)
        2. Voice audio (.mp3 via Edge TTS -> Cloudinary)
        3. Poster image (.png via Pillow -> Cloudinary)
        Each with 2-second gap.
        """
        conn = get_db()
        campaign = conn.execute("""
            SELECT c.*, d.district as district_name, d.state, d.language
            FROM campaigns c
            JOIN districts d ON c.district_id = d.id
            WHERE c.id = ?
        """, (campaign_id,)).fetchone()
        conn.close()
        
        if not campaign:
            return {'error': 'Campaign not found', 'campaign_id': campaign_id}
        
        c = dict(campaign)
        results = []
        errors = []
        needs_switch = False
        
        # === PRE-GENERATE MEDIA ===
        print(f"[DELIVERY] Generating media for campaign #{campaign_id}...")
        
        # Generate voice audio
        voice_url = None
        voice_text = c.get('message_voice_script', '')
        if voice_text:
            _, voice_url = self.media.generate_voice(voice_text, c.get('language', 'Hindi'), campaign_id)
        
        # Generate poster
        poster_url = None
        poster_headline = c.get('poster_headline', '')
        poster_body = c.get('poster_body', '')
        if poster_headline or poster_body:
            _, poster_url = self.media.generate_poster(
                poster_headline, poster_body,
                crop=c.get('crop', ''), disease=c.get('disease', ''),
                product=c.get('product', ''), language=c.get('language', 'Hindi'),
                campaign_id=campaign_id
            )
        
        print(f"[DELIVERY] Media ready. Voice: {'YES' if voice_url else 'NO'} | Poster: {'YES' if poster_url else 'NO'}")
        
        # Save media URLs to campaign record (for history)
        if voice_url or poster_url:
            conn = get_db()
            conn.execute("""
                UPDATE campaigns SET voice_url=?, poster_url=? WHERE id=?
            """, (voice_url, poster_url, campaign_id))
            conn.commit()
            conn.close()
            print(f"[DELIVERY] Media URLs saved to campaign #{campaign_id}")
        
        # === SEND TO EACH NUMBER ===
        for number in self.test_numbers:
            if not number:
                continue
            
            number_results = {'number': number, 'channels': []}
            
            # MESSAGE 1: Text Advisory
            text_msg = self._build_text_message(c)
            r1 = self.send_whatsapp(number, text_msg, campaign_id)
            number_results['channels'].append({'type': 'text', 'result': r1})
            
            if r1.get('status') == 'limited':
                needs_switch = True
                errors.append('Text: ' + r1.get('error', 'Limit hit'))
                results.append(number_results)
                continue  # Don't try more if limited
            
            # 2-second gap
            time.sleep(2)
            
            # MESSAGE 2: Voice Audio
            if voice_url:
                r2 = self.send_whatsapp_media(number, voice_url, 'Voice Advisory', campaign_id)
                number_results['channels'].append({'type': 'voice', 'result': r2})
                if r2.get('status') == 'limited':
                    needs_switch = True
                    errors.append('Voice: limit hit')
                    results.append(number_results)
                    continue
                time.sleep(2)
            
            # MESSAGE 3: Poster Image
            if poster_url:
                r3 = self.send_whatsapp_media(number, poster_url, 'Poster / Wall Writing', campaign_id)
                number_results['channels'].append({'type': 'poster', 'result': r3})
                if r3.get('status') == 'limited':
                    needs_switch = True
                    errors.append('Poster: limit hit')
            
            results.append(number_results)
        
        # Update status
        conn = get_db()
        if needs_switch:
            status = 'pending'
        elif errors:
            status = 'partial'
        else:
            status = 'completed'
        conn.execute("UPDATE campaigns SET status=? WHERE id=?", (status, campaign_id))
        conn.commit()
        conn.close()
        
        sent_count = sum(1 for r in results 
                        for ch in r.get('channels', []) 
                        if ch.get('result', {}).get('status') == 'sent')
        
        return {
            'campaign_id': campaign_id,
            'district': c.get('district_name', 'N/A'),
            'channels_sent': sent_count,
            'voice_generated': voice_url is not None,
            'poster_generated': poster_url is not None,
            'method': 'separate_3channel',
            'needs_switch': needs_switch,
            'active_account': self.get_active_info(),
            'errors': errors,
            'results': results,
        }
    
    def _build_text_message(self, campaign):
        """Build text-only advisory message"""
        parts = []
        parts.append("*KrishiConnect AI - Campaign Alert*")
        parts.append(f"District: *{campaign.get('district_name', 'N/A')}* ({campaign.get('state', '')})")
        parts.append("")
        
        sms = campaign.get('message_sms', '')
        if sms:
            parts.append(sms)
            parts.append("")
        
        wa = campaign.get('message_whatsapp', '')
        if wa:
            parts.append(wa)
            parts.append("")
        
        parts.append("_Powered by KrishiConnect AI x Syngenta India_")
        return "\n".join(parts)
    
    def send_test_message(self, to_number, message="KrishiConnect AI test - Sab kaam kar raha hai!"):
        """Quick test message"""
        return self.send_whatsapp(to_number, message)
    
    def get_sandbox_info(self):
        """Return sandbox info (kept for backward compat)"""
        return self.get_active_info()
    
    def _log_delivery(self, campaign_id, channel, twilio_sid=None, error=None, phone=None):
        """Log delivery attempt with recipient phone for two-way tracking"""
        try:
            conn = get_db()
            conn.execute("""
                INSERT INTO delivery_log 
                (campaign_id, channel, recipient_phone, sent_at, status, twilio_sid, error_message)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
            """, (
                campaign_id, channel, phone,
                'sent' if twilio_sid and twilio_sid != 'simulated' else 'failed',
                twilio_sid, error
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[DELIVERY] Log error: {e}")
    
    def get_delivery_stats(self):
        """Get delivery statistics"""
        conn = get_db()
        stats = {
            'total': conn.execute("SELECT COUNT(*) FROM delivery_log").fetchone()[0],
            'sent': conn.execute("SELECT COUNT(*) FROM delivery_log WHERE status='sent'").fetchone()[0],
            'failed': conn.execute("SELECT COUNT(*) FROM delivery_log WHERE status='failed'").fetchone()[0],
            'by_channel': {},
        }
        channels = conn.execute(
            "SELECT channel, COUNT(*) FROM delivery_log GROUP BY channel"
        ).fetchall()
        for c in channels:
            stats['by_channel'][c[0]] = c[1]
        conn.close()
        return stats
