# 🚀 دليل النشر على Railway + ربط Telegram Bot

## 📋 المتطلبات

قبل البدء، تحتاج:
1. حساب GitHub (لديك بالفعل ✅)
2. حساب Railway (مجاني - https://railway.app)
3. Telegram Bot Token (من @BotFather)
4. (اختياري) مفاتيح API للذكاء الاصطناعي

---

## 🤖 أولاً: إنشاء Telegram Bot

### الخطوة 1: التحدث مع BotFather
1. افتح Telegram وابحث عن: `@BotFather`
2. أرسل الأمر: `/newbot`
3. اتبع التعليمات:
   - أدخل اسم البوت (مثال: `Abdulrahman AI Assistant`)
   - أدخل username للبوت (يجب أن ينتهي بـ `bot`، مثال: `abdulrahman_ai_bot`)

### الخطوة 2: احفظ الـ Token
BotFather سيعطيك token مثل:
```
7123456789:ABCdefGHIjklMNOpqrsTUVwxyz
```

⚠️ **مهم**: احفظ هذا الـ token في مكان آمن! لن يظهر مرة أخرى.

### الخطوة 3: احصل على User ID الخاص بك
1. ابحث عن: `@userinfobot`
2. أرسل أي رسالة
3. سيرد عليك بـ ID رقمي (مثال: `123456789`)

احفظ هذا الـ ID - سنستخدمه للـ `TELEGRAM_ALLOWED_USER_IDS`

---

## 🚀 ثانياً: النشر على Railway

### الخطوة 1: إنشاء حساب Railway
1. اذهب إلى: https://railway.app
2. سجل دخول بحساب GitHub

### الخطوة 2: إنشاء مشروع جديد
1. اضغط: `New Project`
2. اختر: `Deploy from GitHub repo`
3. اختر المستودع: `abdul200200-netizen/personal-ai-agent`
4. اختر الـ branch: `arena/bc8aa06a-personal-ai-agent`

### الخطوة 3: إضافة Environment Variables

في Railway dashboard، اذهب إلى:
- **Variables** → **Raw Editor**
- الصق المتغيرات التالية (عدّل القيم حسب حاجتك):

```bash
# ============= OpenCode AI =============
OPENCODE_MODE=api
OPENCODE_BASE_URL=https://opencode.ai/zen/v1
OPENCODE_API_KEY=public
OPENCODE_MODEL=big-pickle

# ============= Telegram Bot =============
TELEGRAM_BOT_TOKEN=7123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_WEBHOOK_SECRET=your_random_secret_here_123
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_POLLING=true

# ============= Database =============
SQLITE_DB_PATH=/app/data/agent.db
HOST=0.0.0.0
PORT=8000

# ============= Clinical Evidence APIs (اختياري) =============
# NCBI_API_KEY=your_ncbi_key_here
# OPENFDA_API_KEY=your_openfda_key_here
# UMLS_API_KEY=your_umls_key_here
CLINICAL_EVIDENCE_CACHE_TTL=86400
RETRIEVAL_DEFAULT_WINDOW_YEARS=5
```

### الخطوة 4: إضافة Persistent Volume (للقاعدة)
1. في Railway dashboard، اذهب إلى: **Settings** → **Networking**
2. تأكد أن الـ HTTP port هو `8000`
3. اذهب إلى: **Volumes** → **Add Volume**
   - Mount Path: `/app/data`
   - Size: 1GB (أو أكثر حسب الحاجة)

### الخطوة 5: Deploy
1. اضغط: **Deploy**
2. انتظر 2-3 دقائق
3. Railway سيعطيك رابط مثل: `https://personal-ai-agent-production.up.railway.app`

### الخطوة 6: التحقق من النشر
افتح في المتصفح:
```
https://your-app-name.up.railway.app/health
```

يجب أن ترى:
```json
{
  "status": "healthy",
  "provider": "opencode",
  "model": "big-pickle"
}
```

---

## 🔄 ثالثاً: ربط Telegram مع Railway

### الخيار 1: Polling Mode (الأسهل - مُفعّل بالفعل ✅)

إذا ضبطت `TELEGRAM_POLLING=true` في المتغيرات البيئية:
- البوت سيعمل تلقائياً
- لا تحتاج إعدادات إضافية
- مناسب للمبتدئين

### الخيار 2: Webhook Mode (أسرع - للمحترفين)

1. احصل على رابط Railway الخاص بك:
   ```
   https://personal-ai-agent-production.up.railway.app
   ```

2. سجّل الـ webhook مع Telegram:
   ```bash
   curl -X POST "https://api.telegram.org/botYOUR_BOT_TOKEN/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://personal-ai-agent-production.up.railway.app/webhook/telegram",
       "secret_token": "your_random_secret_here_123"
     }'
   ```

3. غيّر في Railway:
   ```bash
   TELEGRAM_POLLING=false
   ```

4. أعد التشغيل (Deploy)

---

## ✅ رابعاً: اختبار البوت

### اختبار 1: رسالة ترحيب
1. افتح Telegram
2. ابحث عن البوت الخاص بك (مثال: `@abdulrahman_ai_bot`)
3. أرسل: `/start`
4. يجب أن يرد عليك البوت برسالة ترحيب

### اختبار 2: حالة النظام
أرسل: `/status`

يجب أن ترى معلومات مثل:
```
🟢 System Status
Provider: OpenCode
Model: big-pickle
Database: SQLite
Telegram: Connected
```

### اختبار 3: بحث طبي
أرسل:
```
ابحث عن أحدث الدراسات عن SGLT2 inhibitors في heart failure
```

يجب أن يرد عليك البوت بنتائج مع citations.

### اختبار 4: اختبار حماية PHI
أرسل:
```
مريضي محمد أحمد، تاريخ ميلاده 15/03/1985، يعاني من السكري
```

يجب أن يرفض البوت ويطلب منك إزالة المعلومات الشخصية.

---

## 🔧 خامساً: الأوامر المتاحة في Telegram

| الأمر | الوصف |
|-------|--------|
| `/start` | رسالة ترحيب |
| `/help` | قائمة الأوامر |
| `/status` | حالة النظام |
| `/tasks` | عرض المهام |
| `/calendar` | عرض أحداث التقويم |
| `/clear` | مسح المحادثة |
| `/evidence` | البحث عن أدلة طبية |
| `/drugs` | البحث عن معلومات الأدوية |

---

## 📊 سادساً: المراقبة والتحديث

### عرض الـ Logs
في Railway dashboard:
- اذهب إلى: **Deployments** → اختر الـ deployment النشط → **View Logs**

### تحديث الكود
1. ارفع التغييرات إلى GitHub
2. Railway سيعيد النشر تلقائياً
3. أو اضغط: **Deploy** → **Redeploy**

### إعادة تشغيل البوت
في Railway dashboard:
- **Deployments** → اختر الـ deployment → **Restart**

---

## 🚨 حل المشاكل الشائعة

### المشكلة: البوت لا يرد
**الحل**:
1. تحقق من `TELEGRAM_BOT_TOKEN` صحيح
2. تحقق من `TELEGRAM_ALLOWED_USER_IDS` يحتوي على ID الخاص بك
3. تحقق من Logs في Railway

### المشكلة: "Unauthorized Telegram user ID"
**الحل**:
1. تأكد أن ID الخاص بك في `TELEGRAM_ALLOWED_USER_IDS`
2. افصل بين عدة IDs بفاصلة: `123456789,987654321`

### المشكلة: البوت بطيء
**الحل**:
1. تحقق من Railway plan (Free tier محدود)
2. فعّل Webhook mode بدلاً من Polling
3. زد الـ `OPENCODE_TIMEOUT` إذا لزم الأمر

### المشكلة: قاعدة البيانات تفقد البيانات
**الحل**:
1. تأكد أن Volume مربوط بـ `/app/data`
2. تحقق من `SQLITE_DB_PATH=/app/data/agent.db`
3. Railway Free tier قد يحذف البيانات بعد فترة - استخدم paid plan

---

## 💰 التكلفة التقديرية

### Railway (Free Tier)
- ✅ 500 ساعة/شهر (كافية لبوت واحد)
- ✅ 5GB bandwidth
- ✅ 1GB storage
- ⚠️ بعد الحد المجاني: $5/شهر

### Telegram Bot
- ✅ مجاني بالكامل

### OpenCode AI
- ✅ Free tier متاح (مع rate limits)
- 💡 إذا احتجت أكثر: اشترك في plan مدفوع

---

## 📝 Checklist قبل النشر

- [ ] إنشاء Telegram Bot والحصول على Token
- [ ] الحصول على User ID الخاص بك
- [ ] إنشاء حساب Railway
- [ ] ربط المستودع مع Railway
- [ ] إضافة كل Environment Variables
- [ ] إضافة Persistent Volume
- [ ] Deploy ونجاح
- [ ] اختبار `/start` في Telegram
- [ ] اختبار بحث طبي
- [ ] اختبار حماية PHI

---

## 🎯 الخطوة التالية

بعد نجاح النشر، يمكنك:
1. إضافة مفاتيح API للذكاء الاصطناعي (OpenAI, Claude, etc.)
2. ربط Google Calendar و Google Sheets
3. تفعيل Clinical Evidence APIs بمفاتيح
4. إضافة ميزات جديدة (المهام، التذكيرات، إلخ)

---

**ملاحظة مهمة**: إذا واجهت أي مشكلة، تحقق من Logs في Railway أولاً - 90% من المشاكل تظهر هناك!
