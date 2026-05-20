# KrishiConnect AI - Media Service
# TTS Cascade: Edge TTS -> gTTS -> ElevenLabs
# Poster: Pillow image generation
# Upload: Cloudinary (public URLs for Twilio)
import os
import asyncio
import uuid
from config import Config


class MediaService:
    """Generate voice audio + poster images, upload to Cloudinary"""
    
    def __init__(self):
        self.cloudinary_ready = False
        self._init_cloudinary()
        self._ensure_dirs()
    
    def _ensure_dirs(self):
        """Create local temp dirs"""
        os.makedirs(Config.AUDIO_DIR, exist_ok=True)
        os.makedirs(Config.POSTER_DIR, exist_ok=True)
    
    def _init_cloudinary(self):
        """Initialize Cloudinary"""
        if not Config.CLOUDINARY_CLOUD_NAME:
            print("[MEDIA] WARNING: Cloudinary not configured")
            return
        try:
            import cloudinary
            cloudinary.config(
                cloud_name=Config.CLOUDINARY_CLOUD_NAME,
                api_key=Config.CLOUDINARY_API_KEY,
                api_secret=Config.CLOUDINARY_API_SECRET,
                secure=True,
            )
            self.cloudinary_ready = True
            print("[MEDIA] Cloudinary connected:", Config.CLOUDINARY_CLOUD_NAME)
        except Exception as e:
            print(f"[MEDIA] Cloudinary error: {e}")
    
    def upload_to_cloudinary(self, file_path, resource_type="auto", folder="krishiconnect"):
        """Upload file to Cloudinary, return public URL"""
        if not self.cloudinary_ready:
            print("[MEDIA] Cloudinary not ready, skipping upload")
            return None
        try:
            import cloudinary.uploader
            result = cloudinary.uploader.upload(
                file_path,
                resource_type=resource_type,
                folder=folder,
                use_filename=True,
                unique_filename=True,
            )
            url = result.get('secure_url', '')
            print(f"[MEDIA] Uploaded: {os.path.basename(file_path)} -> {url}")
            return url
        except Exception as e:
            print(f"[MEDIA] Upload failed: {e}")
            return None
    
    # ════════════════════════════════════════
    # TTS CASCADE: Edge TTS -> gTTS -> basic
    # ════════════════════════════════════════
    
    def generate_voice(self, text, language='Hindi', campaign_id=None):
        """
        Generate voice audio with cascade:
        1. Edge TTS (free, unlimited, best quality)
        2. gTTS (free, unlimited, basic quality)
        Returns: (local_path, cloud_url) or (None, None)
        """
        filename = f"voice_{campaign_id or uuid.uuid4().hex[:8]}.mp3"
        local_path = os.path.join(Config.AUDIO_DIR, filename)
        
        # Try Edge TTS first
        success = self._try_edge_tts(text, language, local_path)
        
        # Fallback: gTTS
        if not success:
            success = self._try_gtts(text, language, local_path)
        
        if not success:
            print(f"[MEDIA] All TTS engines failed for campaign {campaign_id}")
            return None, None
        
        # Upload to Cloudinary
        cloud_url = self.upload_to_cloudinary(local_path, resource_type="video", folder="krishiconnect/audio")
        
        return local_path, cloud_url
    
    def _try_edge_tts(self, text, language, output_path):
        """Edge TTS - FREE, unlimited, good quality"""
        try:
            import edge_tts
            voice = Config.VOICE_MAP.get(language, 'hi-IN-SwaraNeural')
            
            async def _generate():
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(output_path)
            
            # Run async in sync context
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        future = pool.submit(asyncio.run, _generate())
                        future.result(timeout=30)
                else:
                    loop.run_until_complete(_generate())
            except RuntimeError:
                asyncio.run(_generate())
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                size_kb = os.path.getsize(output_path) / 1024
                print(f"[MEDIA] Edge TTS OK: {language} ({voice}), {size_kb:.0f}KB")
                return True
            return False
        except Exception as e:
            print(f"[MEDIA] Edge TTS failed: {e}")
            return False
    
    def _try_gtts(self, text, language, output_path):
        """gTTS - FREE, unlimited, basic quality"""
        try:
            from gtts import gTTS
            lang_map = {
                'Hindi': 'hi', 'Tamil': 'ta', 'Telugu': 'te',
                'Marathi': 'mr', 'Bengali': 'bn', 'Kannada': 'kn',
                'Gujarati': 'gu', 'Malayalam': 'ml', 'Punjabi': 'pa',
                'Odia': 'or', 'Urdu': 'ur', 'Konkani': 'hi',
                'English': 'en',
            }
            lang_code = lang_map.get(language, 'hi')
            tts = gTTS(text=text, lang=lang_code, slow=False)
            tts.save(output_path)
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                size_kb = os.path.getsize(output_path) / 1024
                print(f"[MEDIA] gTTS OK: {language} ({lang_code}), {size_kb:.0f}KB")
                return True
            return False
        except Exception as e:
            print(f"[MEDIA] gTTS failed: {e}")
            return False
    
    # ════════════════════════════════════════
    # POSTER GENERATION (Pillow)
    # ════════════════════════════════════════
    
    def generate_poster(self, headline, body, crop='', disease='', product='',
                        language='Hindi', campaign_id=None):
        """
        Generate premium poster image using Pillow.
        Returns: (local_path, cloud_url) or (None, None)
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            print("[MEDIA] Pillow not installed")
            return None, None
        
        filename = f"poster_{campaign_id or uuid.uuid4().hex[:8]}.png"
        local_path = os.path.join(Config.POSTER_DIR, filename)
        
        try:
            width, height = 640, 900
            img = Image.new('RGB', (width, height), '#0d1b2a')
            draw = ImageDraw.Draw(img)
            
            # Try to load fonts
            try:
                font_title = ImageFont.truetype("arial.ttf", 36)
                font_heading = ImageFont.truetype("arial.ttf", 28)
                font_body = ImageFont.truetype("arial.ttf", 20)
                font_small = ImageFont.truetype("arial.ttf", 15)
                font_tag = ImageFont.truetype("arial.ttf", 13)
            except:
                font_title = ImageFont.load_default()
                font_heading = font_title
                font_body = font_title
                font_small = font_title
                font_tag = font_title
            
            # ── Gradient background (dark blue to dark green) ──
            for i in range(height):
                ratio = i / height
                r = int(13 + (10 - 13) * ratio)
                g = int(27 + (60 - 27) * ratio)
                b = int(42 + (30 - 42) * ratio)
                draw.line([(0, i), (width, i)], fill=(r, g, b))
            
            # ── Top accent strip (Syngenta green) ──
            draw.rectangle([(0, 0), (width, 6)], fill='#00a651')
            
            # ── Logo area ──
            draw.rectangle([(0, 6), (width, 90)], fill=(15, 30, 50, 200))
            # Syngenta brand dot
            draw.ellipse([(24, 24), (56, 56)], fill='#00a651')
            draw.text((34, 27), "S", fill='white', font=font_heading)
            draw.text((68, 20), "KrishiConnect AI", fill='white', font=font_heading)
            draw.text((68, 54), "Syngenta Crop Advisory", fill='#6db88f', font=font_small)
            
            # ── Alert badge ──
            y = 110
            risk_color = '#ef4444' if disease else '#f59e0b'
            # Alert background
            draw.rounded_rectangle([(24, y), (width - 24, y + 80)], radius=12, fill=risk_color)
            # Alert inner shadow line
            draw.line([(24, y + 78), (width - 24, y + 78)], fill=(0, 0, 0, 80), width=2)
            
            alert_icon = "⚠️"
            draw.text((40, y + 12), alert_icon + " CROP ALERT", fill='white', font=font_heading)
            if crop or disease:
                tag_text = f"{crop}  ·  {disease}" if crop and disease else (crop or disease)
                draw.text((40, y + 48), tag_text, fill='#ffe0e0', font=font_body)
            
            y += 100
            
            # ── Headline section ──
            if headline:
                draw.text((30, y), "━" * 28, fill='#00a651', font=font_tag)
                y += 18
                
                words = headline.split()
                lines = []
                current = ""
                for w in words:
                    test = current + " " + w if current else w
                    if len(test) > 26:
                        lines.append(current)
                        current = w
                    else:
                        current = test
                if current:
                    lines.append(current)
                
                for line in lines[:3]:
                    draw.text((30, y), line, fill='#fbbf24', font=font_title)
                    y += 44
                
                y += 8
            
            # ── Body text section ──
            if body:
                # Dark content area (RGB mode doesn't support alpha)
                content_bottom = min(y + 260, height - 160)
                draw.rounded_rectangle(
                    [(20, y), (width - 20, content_bottom)],
                    radius=10,
                    fill=(20, 40, 55),
                    outline=(40, 80, 70)
                )
                
                body_y = y + 16
                # Handle newlines in body
                body_lines = []
                for raw_line in body.split('\n'):
                    raw_line = raw_line.strip()
                    if not raw_line:
                        body_lines.append('')
                        continue
                    words = raw_line.split()
                    current = ""
                    for w in words:
                        test = current + " " + w if current else w
                        if len(test) > 36:
                            body_lines.append(current)
                            current = w
                        else:
                            current = test
                    if current:
                        body_lines.append(current)
                
                for line in body_lines[:10]:
                    if body_y > content_bottom - 30:
                        break
                    if not line:
                        body_y += 10
                        continue
                    # Highlight key labels
                    if ':' in line and line.index(':') < 15:
                        label, val = line.split(':', 1)
                        label_text = label + ":"
                        draw.text((36, body_y), label_text, fill='#6db88f', font=font_body)
                        try:
                            label_w = draw.textlength(label_text, font=font_body)
                        except:
                            label_w = len(label_text) * 10
                        draw.text((36 + label_w + 8, body_y), val.strip(), fill='white', font=font_body)
                    else:
                        draw.text((36, body_y), line, fill='#e0e0e0', font=font_body)
                    body_y += 28
                
                y = content_bottom + 16
            
            # ── Product recommendation box ──
            if product:
                draw.rounded_rectangle(
                    [(20, y), (width - 20, y + 72)],
                    radius=10,
                    fill='#00a651',
                )
                # Green dot indicator
                draw.ellipse([(32, y + 14), (46, y + 28)], fill='#4ade80')
                draw.text((54, y + 12), "Recommended Product", fill='#c8e6c9', font=font_small)
                draw.text((54, y + 34), product, fill='white', font=font_heading)
                y += 88
            
            # ── Bottom branding ──
            # Dark footer strip
            footer_y = height - 80
            draw.rectangle([(0, footer_y), (width, height)], fill=(8, 18, 30))
            # Green accent line
            draw.rectangle([(0, footer_y), (width, footer_y + 3)], fill='#00a651')
            # Footer text
            draw.text((24, footer_y + 16), "Powered by KrishiConnect AI", fill='#6db88f', font=font_small)
            draw.text((24, footer_y + 38), "Syngenta India  ·  Helpline: 1800-123-4567", fill='#4a7a60', font=font_tag)
            # Green dot bottom-right
            draw.ellipse([(width - 50, footer_y + 20), (width - 24, footer_y + 46)], fill='#00a651')
            draw.text((width - 44, footer_y + 23), "S", fill='white', font=font_small)
            
            # Save
            img.save(local_path, 'PNG', quality=95)
            size_kb = os.path.getsize(local_path) / 1024
            print(f"[MEDIA] Poster OK: {size_kb:.0f}KB ({width}x{height})")
            
            # Upload to Cloudinary
            cloud_url = self.upload_to_cloudinary(local_path, resource_type="image", folder="krishiconnect/posters")
            
            return local_path, cloud_url
            
        except Exception as e:
            print(f"[MEDIA] Poster failed: {e}")
            return None, None
    
    # ════════════════════════════════════════
    # CLEANUP (on admin reset)
    # ════════════════════════════════════════
    
    def cleanup_all(self):
        """Delete all media from Cloudinary + local files. Called on admin reset."""
        deleted = {'cloudinary': 0, 'local': 0}
        
        # 1. Delete Cloudinary folder
        if self.cloudinary_ready:
            try:
                import cloudinary.api
                # Delete all resources in krishiconnect folder
                for resource_type in ['image', 'video']:
                    try:
                        result = cloudinary.api.delete_resources_by_prefix(
                            'krishiconnect/', resource_type=resource_type
                        )
                        count = len(result.get('deleted', {}))
                        deleted['cloudinary'] += count
                        print(f"[MEDIA] Cloudinary: deleted {count} {resource_type}(s)")
                    except Exception as e:
                        print(f"[MEDIA] Cloudinary {resource_type} cleanup: {e}")
                
                # Try to delete empty folders
                try:
                    cloudinary.api.delete_folder('krishiconnect/audio')
                except: pass
                try:
                    cloudinary.api.delete_folder('krishiconnect/posters')
                except: pass
                try:
                    cloudinary.api.delete_folder('krishiconnect')
                except: pass
                
            except Exception as e:
                print(f"[MEDIA] Cloudinary cleanup error: {e}")
        
        # 2. Delete local files
        import shutil
        for dir_path in [Config.AUDIO_DIR, Config.POSTER_DIR]:
            if os.path.exists(dir_path):
                for f in os.listdir(dir_path):
                    try:
                        os.remove(os.path.join(dir_path, f))
                        deleted['local'] += 1
                    except: pass
        
        print(f"[MEDIA] Cleanup done: {deleted['cloudinary']} cloud + {deleted['local']} local files")
        return deleted
