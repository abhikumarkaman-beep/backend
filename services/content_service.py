# KrishiConnect AI - Content Generation Service (Gemini + Edge TTS)
import os
import json
import asyncio
import hashlib
from datetime import datetime
from config import Config
from database import get_db


class ContentService:
    """Generate multilingual campaign content using Gemini API"""
    
    # Static reply options appended to every WhatsApp message (Hindi + English)
    WHATSAPP_REPLY_FOOTER = (
        "\n\n---\n"
        "📩 *Jawab dein / Reply:*\n"
        "*1* → अधिक जानकारी / More info\n"
        "*2* → विशेषज्ञ से बात / Talk to expert\n"
        "*3* → उत्पाद खरीदना है / Want to buy\n"
        "*4* → खेत में समस्या दिखी / Field issue seen"
    )
    
    def __init__(self):
        self.gemini_available = False
        self.models = []  # Multiple models for key rotation
        self.model = None
        self.current_key_index = 0
        self.content_cache = {}  # Cache generated content
        self._init_gemini()
    
    def _init_gemini(self):
        """Initialize Gemini API with multiple key rotation"""
        keys = Config.GEMINI_API_KEYS
        if not keys:
            print("[CONTENT] No Gemini API keys. Using template fallback.")
            return
        try:
            import google.generativeai as genai
            for i, key in enumerate(keys):
                genai.configure(api_key=key)
                model = genai.GenerativeModel('gemini-2.0-flash')
                self.models.append({'key': key[:8] + '...', 'model': model, 'genai': genai})
            self.model = self.models[0]['model']
            self.gemini_available = True
            print(f"[CONTENT] Gemini connected: {len(self.models)} API key(s) loaded.")
        except ImportError:
            print("[CONTENT] google-generativeai not installed. Using template fallback.")
        except Exception as e:
            print(f"[CONTENT] Gemini init error: {e}. Using template fallback.")
    
    def warm_pipeline_cache(self, prediction):
        """Fast template content for batch pipeline (no Gemini latency)."""
        cache_key = f"{prediction['crop']}_{prediction['disease']}_{prediction.get('language', 'Hindi')}"
        if cache_key not in self.content_cache:
            content = self._template_generate(prediction)
            if content.get('whatsapp'):
                content['whatsapp'] = content['whatsapp'].rstrip() + self.WHATSAPP_REPLY_FOOTER
            content['method'] = 'template_pipeline'
            self.content_cache[cache_key] = content
        return self.content_cache[cache_key]
    
    def get_cached_content(self, prediction):
        """Return cached content for prediction combo, or None."""
        cache_key = f"{prediction['crop']}_{prediction['disease']}_{prediction.get('language', 'Hindi')}"
        return self.content_cache.get(cache_key)
    
    def generate_campaign_content(self, prediction):
        """Generate SMS, WhatsApp, Voice content for a disease prediction.
        After generation, appends static reply options to WhatsApp message."""
        cached = self.get_cached_content(prediction)
        if cached:
            return cached.copy()
        if self.gemini_available:
            content = self._gemini_generate(prediction)
        else:
            content = self._template_generate(prediction)
        
        # Append static reply options to WhatsApp message (guaranteed to be there)
        if content.get('whatsapp'):
            content['whatsapp'] = content['whatsapp'].rstrip() + self.WHATSAPP_REPLY_FOOTER
        
        return content
    
    def _gemini_generate(self, pred):
        """Use Gemini to generate localized content"""
        prompt = f"""You are an agricultural advisory expert for Syngenta India.
Generate farmer-friendly messages in {pred['language']} language for the following alert:

CONTEXT:
- District: {pred['district']}, {pred['state']}
- Crop: {pred['crop']}
- Disease Risk: {pred['disease']} ({pred['probability']*100:.0f}% probability)
- Risk Level: {pred['risk_level']}
- Recommended Product: {pred['product']} (Dosage: {pred['dosage']})
- Application: {pred.get('application', 'Foliar spray')}
- Weather: Temperature {pred['weather_summary'].get('avg_temp', 'N/A')}C, 
  Humidity {pred['weather_summary'].get('avg_humidity', 'N/A')}%, 
  Rainfall {pred['weather_summary'].get('total_rainfall', 'N/A')}mm
{"- NOTE: This is a VIRAL disease. The product kills the vector insect, not the disease itself." if pred.get('is_viral') else ""}
- Retailer Info: Syngenta Helpline 1800-123-4567 (Toll Free) | www.syngenta.co.in/retailer-locator

Generate EXACTLY this JSON format (no extra text):
{{
  "sms": "Short SMS in {pred['language']} (max 160 chars). Include crop, disease risk, product name, and Syngenta helpline 1800-123-4567.",
  "whatsapp": "Detailed WhatsApp message in {pred['language']} (max 900 chars). Include greeting, crop name, disease warning, weather reason, product recommendation with dosage, application method, retailer info (Syngenta Helpline: 1800-123-4567, Toll Free), and disclaimer. Do NOT include reply options at the end - they will be appended automatically.",
  "voice_script": "Natural conversational voice script in {pred['language']} (30-40 seconds when spoken). Start with 'Namaste kisan bhai' or equivalent greeting. Include all details in simple language. End with helpline number 1800-123-4567.",
  "poster_headline": "Bold headline in {pred['language']} (max 8 words)",
  "poster_body": "Poster body text in {pred['language']} (max 100 words). Key points with product info and helpline 1800-123-4567."
}}

CRITICAL RULES:
- Write in {pred['language']} script (Devanagari for Hindi, Tamil script for Tamil, etc.)
- Use simple farmer-friendly language, no technical jargon
- Always include product name and dosage
- Always include: Syngenta Helpline 1800-123-4567 (Toll Free)
- Do NOT add any reply/feedback options (1/2/3/4) in WhatsApp — they are appended automatically after generation
- Add disclaimer: weather-based advisory, consult local expert
- NEVER guarantee crop saving, say "risk hai" or "sambhavna hai"
"""
        # Check cache first (same crop+disease+language = same content)
        cache_key = f"{pred['crop']}_{pred['disease']}_{pred.get('language','Hindi')}"
        if cache_key in self.content_cache:
            cached = self.content_cache[cache_key].copy()
            cached['method'] = 'gemini_cached'
            return cached
        
        # Try each API key (rotate on rate limit)
        last_error = None
        for attempt in range(len(self.models)):
            idx = (self.current_key_index + attempt) % len(self.models)
            model_info = self.models[idx]
            try:
                model_info['genai'].configure(api_key=model_info['key'].replace('...', '') if '...' not in model_info['key'] else Config.GEMINI_API_KEYS[idx])
                response = self.models[idx]['model'].generate_content(prompt)
                text = response.text.strip()
                
                # Parse JSON from response
                if '```json' in text:
                    text = text.split('```json')[1].split('```')[0].strip()
                elif '```' in text:
                    text = text.split('```')[1].split('```')[0].strip()
                
                content = json.loads(text)
                content['method'] = 'gemini'
                
                # Cache for future use
                self.content_cache[cache_key] = content.copy()
                self.current_key_index = idx  # Remember working key
                return content
                
            except Exception as e:
                last_error = str(e)
                if '429' in str(e) or 'quota' in str(e).lower():
                    print(f"[CONTENT] Key #{idx+1} rate limited. Trying next...")
                    continue
                else:
                    break
        
        print(f"[CONTENT] All Gemini keys failed: {last_error}. Using template.")
        return self._template_generate(pred)
    
    def _template_generate(self, pred):
        """Template-based fallback when Gemini is not available"""
        
        d = pred['district']
        st = pred['state']
        cr = pred['crop']
        ds = pred['disease']
        rl = pred['risk_level']
        pr = pred['product']
        do = pred['dosage']
        ap = pred.get('application', 'Foliar spray')
        tp = pred['weather_summary'].get('avg_temp', 'N/A')
        hm = pred['weather_summary'].get('avg_humidity', 'N/A')
        pb = pred['probability'] * 100
        hl = '1800-123-4567'
        
        templates = {
            'Hindi': {
                'sms': f"{d} me {cr} me {ds} ka khatra ({rl}). {pr} {do} spray karein. Helpline: {hl}",
                'whatsapp': (
                    f"Namaste Kisan Bhai,\n\n"
                    f"Aapke kshetra {d}, {st} me mausam ke anusaar "
                    f"{cr} ki fasal me *{ds}* ka khatra hai.\n\n"
                    f"Risk Level: *{rl}* ({pb:.0f}%)\n\n"
                    f"Mausam: Taapman {tp}C, Nami {hm}%\n\n"
                    f"Upay: *{pr}* ka {do} ki dar se {ap} karein.\n\n"
                    f"Nazdeeki retailer ke liye:\n"
                    f"Syngenta Helpline: *{hl}* (Toll Free)\n"
                    f"Website: www.syngenta.co.in/retailer-locator\n\n"
                    f"_Yeh Syngenta ki mausam-based salah hai. Kshetri visheshagya se sampark karein._"
                ),
                'voice_script': (
                    f"Namaste kisan bhai. Syngenta ki taraf se zaroori sandesh. "
                    f"Aapke kshetra {d} me {cr} ki fasal me {ds} ka khatra hai. Risk level {rl} hai. "
                    f"{pr} ka {do} se {ap} karein. Helpline {hl} pe call karein. Dhanyavaad."
                ),
                'poster_headline': f"{cr} me {ds} ka khatra!",
                'poster_body': f"Kshetra: {d}\nFasal: {cr}\nBimari: {ds}\nRisk: {rl}\n\nUpay: {pr} {do}\nTarika: {ap}\n\nSyngenta Helpline: {hl}\nSyngenta - Fasal Suraksha",
            },
            'Telugu': {
                'sms': f"{d}లో {cr}లో {ds} ప్రమాదం ({rl}). {pr} {do} స్ప్రే చేయండి. Helpline: {hl}",
                'whatsapp': (
                    f"నమస్కారం రైతు అన్నా,\n\n"
                    f"మీ ప్రాంతం {d}, {st}లో వాతావరణం ప్రకారం "
                    f"{cr} పంటకు *{ds}* వ్యాధి ప్రమాదం ఉంది.\n\n"
                    f"రిస్క్ లెవల్: *{rl}* ({pb:.0f}%)\n\n"
                    f"వాతావరణం: ఉష్ణోగ్రత {tp}°C, తేమ {hm}%\n\n"
                    f"పరిష్కారం: *{pr}* {do} మోతాదులో {ap} చేయండి.\n\n"
                    f"సమీప రిటైలర్ కోసం:\nSyngenta Helpline: *{hl}* (టోల్ ఫ్రీ)\n\n"
                    f"_ఇది Syngenta వాతావరణ ఆధారిత సలహా. స్థానిక వ్యవసాయ నిపుణులను సంప్రదించండి._"
                ),
                'voice_script': f"నమస్కారం రైతు అన్నా. Syngenta నుండి ముఖ్యమైన సందేశం. మీ ప్రాంతం {d}లో {cr} పంటకు {ds} వ్యాధి ప్రమాదం ఉంది. {pr} {do} స్ప్రే చేయండి. Helpline {hl}. ధన్యవాదాలు.",
                'poster_headline': f"{cr}లో {ds} ప్రమాదం!",
                'poster_body': f"ప్రాంతం: {d}\nపంట: {cr}\nవ్యాధి: {ds}\nరిస్క్: {rl}\n\nపరిష్కారం: {pr} {do}\nSyngenta Helpline: {hl}",
            },
            'Tamil': {
                'sms': f"{d}ல் {cr}ல் {ds} ஆபத்து ({rl}). {pr} {do} தெளிக்கவும். Helpline: {hl}",
                'whatsapp': (
                    f"வணக்கம் விவசாயி அண்ணா,\n\n"
                    f"உங்கள் பகுதி {d}, {st}ல் வானிலை அடிப்படையில் "
                    f"{cr} பயிருக்கு *{ds}* நோய் ஆபத்து உள்ளது.\n\n"
                    f"ஆபத்து நிலை: *{rl}* ({pb:.0f}%)\n\n"
                    f"வானிலை: வெப்பநிலை {tp}°C, ஈரப்பதம் {hm}%\n\n"
                    f"தீர்வு: *{pr}* {do} அளவில் {ap} செய்யவும்.\n\n"
                    f"அருகிலுள்ள சில்லறை விற்பனையாளர்:\nSyngenta Helpline: *{hl}* (கட்டணமில்லா)\n\n"
                    f"_இது Syngenta வானிலை ஆலோசனை. உள்ளூர் வேளாண் நிபுணரை தொடர்புகொள்ளவும்._"
                ),
                'voice_script': f"வணக்கம் விவசாயி அண்ணா. Syngenta சார்பில் முக்கிய செய்தி. உங்கள் பகுதி {d}ல் {cr} பயிருக்கு {ds} நோய் ஆபத்து உள்ளது. {pr} {do} தெளிக்கவும். Helpline {hl}. நன்றி.",
                'poster_headline': f"{cr}ல் {ds} ஆபத்து!",
                'poster_body': f"பகுதி: {d}\nபயிர்: {cr}\nநோய்: {ds}\nஆபத்து: {rl}\n\nதீர்வு: {pr} {do}\nSyngenta Helpline: {hl}",
            },
            'Marathi': {
                'sms': f"{d} मध्ये {cr} वर {ds} चा धोका ({rl}). {pr} {do} फवारणी करा. Helpline: {hl}",
                'whatsapp': (
                    f"नमस्कार शेतकरी बंधू,\n\n"
                    f"तुमच्या भागात {d}, {st} मध्ये हवामानानुसार "
                    f"{cr} पिकावर *{ds}* रोगाचा धोका आहे.\n\n"
                    f"धोक्याची पातळी: *{rl}* ({pb:.0f}%)\n\n"
                    f"हवामान: तापमान {tp}°C, आर्द्रता {hm}%\n\n"
                    f"उपाय: *{pr}* {do} प्रमाणात {ap} करा.\n\n"
                    f"जवळचा रिटेलर:\nSyngenta Helpline: *{hl}* (टोल फ्री)\n\n"
                    f"_हा Syngenta चा हवामान-आधारित सल्ला आहे. स्थानिक कृषी तज्ञांशी संपर्क साधा._"
                ),
                'voice_script': f"नमस्कार शेतकरी बंधू. Syngenta कडून महत्त्वाचा संदेश. तुमच्या भागात {d} मध्ये {cr} पिकावर {ds} रोगाचा धोका आहे. {pr} {do} फवारणी करा. Helpline {hl}. धन्यवाद.",
                'poster_headline': f"{cr} वर {ds} चा धोका!",
                'poster_body': f"भाग: {d}\nपीक: {cr}\nरोग: {ds}\nधोका: {rl}\n\nउपाय: {pr} {do}\nSyngenta Helpline: {hl}",
            },
            'Kannada': {
                'sms': f"{d}ನಲ್ಲಿ {cr}ನಲ್ಲಿ {ds} ಅಪಾಯ ({rl}). {pr} {do} ಸಿಂಪಡಿಸಿ. Helpline: {hl}",
                'whatsapp': (
                    f"ನಮಸ್ಕಾರ ರೈತ ಅಣ್ಣ,\n\n"
                    f"ನಿಮ್ಮ ಪ್ರದೇಶ {d}, {st}ನಲ್ಲಿ ಹವಾಮಾನ ಆಧಾರದ ಮೇಲೆ "
                    f"{cr} ಬೆಳೆಗೆ *{ds}* ರೋಗದ ಅಪಾಯವಿದೆ.\n\n"
                    f"ಅಪಾಯ ಮಟ್ಟ: *{rl}* ({pb:.0f}%)\n\n"
                    f"ಪರಿಹಾರ: *{pr}* {do} ಪ್ರಮಾಣದಲ್ಲಿ {ap} ಮಾಡಿ.\n\n"
                    f"Syngenta Helpline: *{hl}* (ಉಚಿತ)\n\n"
                    f"_ಇದು Syngenta ಹವಾಮಾನ ಸಲಹೆ. ಸ್ಥಳೀಯ ತಜ್ಞರನ್ನು ಸಂಪರ್ಕಿಸಿ._"
                ),
                'voice_script': f"ನಮಸ್ಕಾರ ರೈತ ಅಣ್ಣ. Syngenta ಕಡೆಯಿಂದ ಮುಖ್ಯ ಸಂದೇಶ. {d}ನಲ್ಲಿ {cr} ಬೆಳೆಗೆ {ds} ರೋಗದ ಅಪಾಯ. {pr} {do} ಸಿಂಪಡಿಸಿ. Helpline {hl}. ಧನ್ಯವಾದ.",
                'poster_headline': f"{cr}ನಲ್ಲಿ {ds} ಅಪಾಯ!",
                'poster_body': f"ಪ್ರದೇಶ: {d}\nಬೆಳೆ: {cr}\nರೋಗ: {ds}\nಅಪಾಯ: {rl}\n\nಪರಿಹಾರ: {pr} {do}\nSyngenta Helpline: {hl}",
            },
            'Gujarati': {
                'sms': f"{d}માં {cr}માં {ds} નો ખતરો ({rl}). {pr} {do} છંટકાવ કરો. Helpline: {hl}",
                'whatsapp': (
                    f"નમસ્તે ખેડૂત ભાઈ,\n\n"
                    f"તમારા વિસ્તાર {d}, {st}માં હવામાન મુજબ "
                    f"{cr} ના પાકમાં *{ds}* રોગનો ખતરો છે.\n\n"
                    f"જોખમ સ્તર: *{rl}* ({pb:.0f}%)\n\n"
                    f"ઉપાય: *{pr}* {do} ના દરે {ap} કરો.\n\n"
                    f"Syngenta Helpline: *{hl}* (ટોલ ફ્રી)\n\n"
                    f"_આ Syngenta ની હવામાન આધારિત સલાહ છે._"
                ),
                'voice_script': f"નમસ્તે ખેડૂત ભાઈ. Syngenta તરફથી મહત્વનો સંદેશ. {d}માં {cr} પાકમાં {ds} રોગનો ખતરો. {pr} {do} છંટકાવ કરો. Helpline {hl}. ધન્યવાદ.",
                'poster_headline': f"{cr}માં {ds} ખતરો!",
                'poster_body': f"વિસ્તાર: {d}\nપાક: {cr}\nરોગ: {ds}\nજોખમ: {rl}\n\nઉપાય: {pr} {do}\nSyngenta Helpline: {hl}",
            },
            'Punjabi': {
                'sms': f"{d} ਵਿੱਚ {cr} ਤੇ {ds} ਦਾ ਖ਼ਤਰਾ ({rl}). {pr} {do} ਛਿੜਕਾਅ ਕਰੋ. Helpline: {hl}",
                'whatsapp': (
                    f"ਸਤ ਸ੍ਰੀ ਅਕਾਲ ਕਿਸਾਨ ਭਾਈ,\n\n"
                    f"ਤੁਹਾਡੇ ਇਲਾਕੇ {d}, {st} ਵਿੱਚ ਮੌਸਮ ਮੁਤਾਬਕ "
                    f"{cr} ਦੀ ਫ਼ਸਲ ਤੇ *{ds}* ਬੀਮਾਰੀ ਦਾ ਖ਼ਤਰਾ ਹੈ.\n\n"
                    f"ਖ਼ਤਰਾ ਪੱਧਰ: *{rl}* ({pb:.0f}%)\n\n"
                    f"ਉਪਾਅ: *{pr}* {do} ਦੀ ਦਰ ਨਾਲ {ap} ਕਰੋ.\n\n"
                    f"Syngenta Helpline: *{hl}* (ਮੁਫ਼ਤ)\n\n"
                    f"_ਇਹ Syngenta ਦੀ ਮੌਸਮ ਆਧਾਰਿਤ ਸਲਾਹ ਹੈ._"
                ),
                'voice_script': f"ਸਤ ਸ੍ਰੀ ਅਕਾਲ ਕਿਸਾਨ ਭਾਈ. Syngenta ਵੱਲੋਂ ਜ਼ਰੂਰੀ ਸੁਨੇਹਾ. {d} ਵਿੱਚ {cr} ਤੇ {ds} ਦਾ ਖ਼ਤਰਾ. {pr} {do} ਛਿੜਕਾਅ ਕਰੋ. Helpline {hl}. ਧੰਨਵਾਦ.",
                'poster_headline': f"{cr} ਤੇ {ds} ਦਾ ਖ਼ਤਰਾ!",
                'poster_body': f"ਇਲਾਕਾ: {d}\nਫ਼ਸਲ: {cr}\nਬੀਮਾਰੀ: {ds}\nਖ਼ਤਰਾ: {rl}\n\nਉਪਾਅ: {pr} {do}\nSyngenta Helpline: {hl}",
            },
            'Bengali': {
                'sms': f"{d}-তে {cr}-এ {ds} ঝুঁকি ({rl}). {pr} {do} স্প্রে করুন. Helpline: {hl}",
                'whatsapp': (
                    f"নমস্কার কৃষক ভাই,\n\n"
                    f"আপনার এলাকা {d}, {st}-তে আবহাওয়া অনুযায়ী "
                    f"{cr} ফসলে *{ds}* রোগের ঝুঁকি আছে.\n\n"
                    f"ঝুঁকি স্তর: *{rl}* ({pb:.0f}%)\n\n"
                    f"প্রতিকার: *{pr}* {do} হারে {ap} করুন.\n\n"
                    f"Syngenta Helpline: *{hl}* (টোল ফ্রি)\n\n"
                    f"_এটি Syngenta-র আবহাওয়া ভিত্তিক পরামর্শ._"
                ),
                'voice_script': f"নমস্কার কৃষক ভাই. Syngenta-র পক্ষ থেকে গুরুত্বপূর্ণ বার্তা. {d}-তে {cr} ফসলে {ds} ঝুঁকি. {pr} {do} স্প্রে করুন. Helpline {hl}. ধন্যবাদ.",
                'poster_headline': f"{cr}-এ {ds} ঝুঁকি!",
                'poster_body': f"এলাকা: {d}\nফসল: {cr}\nরোগ: {ds}\nঝুঁকি: {rl}\n\nপ্রতিকার: {pr} {do}\nSyngenta Helpline: {hl}",
            },
            'Odia': {
                'sms': f"{d}ରେ {cr}ରେ {ds} ବିପଦ ({rl}). {pr} {do} ସ୍ପ୍ରେ କରନ୍ତୁ. Helpline: {hl}",
                'whatsapp': (
                    f"ନମସ୍କାର ଚାଷୀ ଭାଇ,\n\n"
                    f"ଆପଣଙ୍କ ଅଞ୍ଚଳ {d}, {st}ରେ ପାଣିପାଗ ଅନୁସାରେ "
                    f"{cr} ଫସಲରେ *{ds}* ରୋଗର ବିପଦ ଅଛି.\n\n"
                    f"ବିପଦ ସ୍ତର: *{rl}* ({pb:.0f}%)\n\n"
                    f"ଉପାୟ: *{pr}* {do} ହାରରେ {ap} କରନ୍ତୁ.\n\n"
                    f"Syngenta Helpline: *{hl}* (ଟୋଲ ଫ୍ରି)\n\n"
                    f"_ଏହା Syngenta ର ପାଣିପାଗ ଆଧାରିତ ପରାମର୍ଶ._"
                ),
                'voice_script': f"ନମସ୍କାର ଚାଷୀ ଭାଇ. Syngenta ତରଫରୁ ଗୁରୁତ୍ୱପୂର୍ଣ୍ଣ ସନ୍ଦେଶ. {d}ରେ {cr} ଫସଲରେ {ds} ବିପଦ. {pr} {do} ସ୍ପ୍ରେ କରନ୍ତୁ. Helpline {hl}. ଧନ୍ୟବାଦ.",
                'poster_headline': f"{cr}ରେ {ds} ବିପଦ!",
                'poster_body': f"ଅଞ୍ଚଳ: {d}\nଫସଲ: {cr}\nରୋଗ: {ds}\nବିପଦ: {rl}\n\nଉପାୟ: {pr} {do}\nSyngenta Helpline: {hl}",
            },
        }
        
        lang = pred.get('language', 'Hindi')
        content = templates.get(lang, templates['Hindi'])
        content['method'] = 'template'
        return content
    
    def generate_ab_variants(self, prediction):
        """Generate 2 message variants for A/B testing"""
        if self.gemini_available:
            return self._gemini_ab_variants(prediction)
        
        # Template-based A/B
        base = self._template_generate(prediction)
        variant_a = base.copy()
        variant_a['whatsapp'] = base['whatsapp']  # informational
        
        variant_b = base.copy()
        # Add urgency to variant B
        variant_b['whatsapp'] = (
            f"!! ZAROORI SUCHNA !!\n\n"
            f"Kisan Bhai, aapki {prediction['crop']} ki fasal khatre me hai!\n\n"
            + base['whatsapp']
        )
        
        return {'variant_a': variant_a, 'variant_b': variant_b}
    
    def _gemini_ab_variants(self, pred):
        """Use Gemini to create A/B test variants"""
        prompt = f"""Generate 2 WhatsApp message variants in {pred['language']} for A/B testing:

Context: {pred['disease']} risk in {pred['district']} for {pred['crop']}. Product: {pred['product']} {pred['dosage']}.

VARIANT A - Informational tone: Facts-based, calm, professional
VARIANT B - Urgency tone: Create urgency (don't panic), emotional connection to family/livelihood

Return JSON: {{"variant_a": "...", "variant_b": "..."}}
"""
        try:
            response = self.model.generate_content(prompt)
            text = response.text.strip()
            if '```json' in text:
                text = text.split('```json')[1].split('```')[0].strip()
            elif '```' in text:
                text = text.split('```')[1].split('```')[0].strip()
            return json.loads(text)
        except Exception:
            base = self._template_generate(pred)
            return {
                'variant_a': base.copy(),
                'variant_b': {
                    **base,
                    'whatsapp': (
                        f"!! ZAROORI SUCHNA !!\n\n"
                        f"Kisan Bhai, aapki {pred['crop']} ki fasal khatre me hai!\n\n"
                        + base['whatsapp']
                    ),
                },
            }


class VoiceService:
    """Generate voice messages using Edge TTS (FREE, unlimited)"""
    
    def __init__(self):
        self.voice_map = Config.VOICE_MAP
        self.audio_dir = Config.AUDIO_DIR
        os.makedirs(self.audio_dir, exist_ok=True)
    
    async def _generate_async(self, text, language, output_path):
        """Internal async voice generation"""
        import edge_tts
        voice = self.voice_map.get(language, 'hi-IN-SwaraNeural')
        communicate = edge_tts.Communicate(text, voice, rate="-10%")
        await communicate.save(output_path)
        return output_path
    
    def generate_voice(self, text, language, campaign_id):
        """Generate voice MP3 file"""
        filename = f"voice_{campaign_id}.mp3"
        output_path = os.path.join(self.audio_dir, filename)
        
        try:
            asyncio.run(self._generate_async(text, language, output_path))
            return output_path
        except ImportError:
            print("[VOICE] edge-tts not installed. Run: pip install edge-tts")
            return None
        except Exception as e:
            print(f"[VOICE] Error: {e}")
            return None
    
    def generate_voice_sync(self, text, language, campaign_id):
        """Sync wrapper for voice generation"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            filename = f"voice_{campaign_id}.mp3"
            output_path = os.path.join(self.audio_dir, filename)
            loop.run_until_complete(self._generate_async(text, language, output_path))
            loop.close()
            return output_path
        except Exception as e:
            print(f"[VOICE] Error: {e}")
            return None
