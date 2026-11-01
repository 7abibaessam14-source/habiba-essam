"""
i18n.py - English / Arabic. The app is written in English; T("English text") gives the Arabic text when the
user picked العربية (and the English text itself when there is no translation yet). Texts with {name} are
filled in: T("Expected for {month}", month=...). Dates: month_year() / day_month() / weekday().

Stage A (translated): login, menu, sidebar, Home, Bill & Budget, Reports, and the reports themselves.
The other pages show an "English for now" note in Arabic mode.
"""

AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
AR_DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]  # Monday first, like pandas
TRANSLATED_PAGES = {"home", "bill", "reports"}


def lang():
    try:
        import streamlit as st

        return st.session_state.get("lang", "en")
    except Exception:  # outside Streamlit (GitHub Actions, tests)
        return "en"


def is_ar(code=None):
    return (code or lang()) == "ar"


def T(text, **kw):
    s = AR.get(text, text) if is_ar() else text
    return s.format(**kw) if kw else s


def month_year(ts, code=None):
    return f"{AR_MONTHS[ts.month - 1]} {ts.year}" if is_ar(code) else f"{ts:%B %Y}"


def month_name(ts, code=None):
    return AR_MONTHS[ts.month - 1] if is_ar(code) else f"{ts:%B}"


def day_month(ts, code=None):
    return f"{ts.day} {AR_MONTHS[ts.month - 1]}" if is_ar(code) else f"{ts:%d %b}"


def weekday(ts, code=None):
    return AR_DAYS[ts.dayofweek] if is_ar(code) else f"{ts:%a}"


def language_switch(container=None):
    """English | العربية switch; the choice is kept in st.session_state.lang for the whole visit."""
    import streamlit as st

    (container or st).radio("Language / اللغة", ["en", "ar"], key="lang", horizontal=True,
                            format_func=lambda c: {"en": "English", "ar": "العربية"}[c], label_visibility="collapsed")


RTL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&display=swap');
[data-testid="stMain"], [data-testid="stSidebarContent"] { direction: rtl; }
[data-testid="stMain"] p, [data-testid="stMain"] li, [data-testid="stMain"] h1, [data-testid="stMain"] h2,
[data-testid="stMain"] h3, [data-testid="stMain"] label, [data-testid="stSidebarContent"] p,
[data-testid="stSidebarContent"] span[data-testid="stSidebarNavLinkContainer"] span, [data-testid="stMetricValue"] div,
[data-testid="stMetricDelta"] div { font-family: 'Cairo', 'Segoe UI', sans-serif; }
[data-testid="stVegaLiteChart"], [data-testid="stCode"], [data-testid="stDataFrame"], [data-testid="stDataFrameResizable"],
[data-testid="stTextInputRootElement"] input[type="password"] { direction: ltr; }
</style>
"""


AR = {
    # ---- menu, sidebar, page titles and intros
    "Overview": "نظرة عامة",
    "Analysis": "التحليل",
    "Machine learning": "تعلّم الآلة",
    "Monitoring": "المراقبة",
    "Project": "المشروع",
    "Home": "الرئيسية",
    "Bill & Budget": "الفاتورة والميزانية",
    "Reports": "التقارير",
    "Analytics": "التحليلات",
    "Appliances": "الأجهزة",
    "Forecast": "التنبؤ",
    "Anomalies": "القراءات غير الطبيعية",
    "Alerts": "التنبيهات",
    "Live": "المراقبة المباشرة",
    "Pipeline monitor": "مراقبة خط البيانات",
    "About project": "عن المشروع",
    "Your bill in Egyptian pounds, the expected bill at month end, budget alerts and saving tips.":
        "فاتورتك بالجنيه المصري، والفاتورة المتوقعة في نهاية الشهر، وتنبيهات الميزانية، ونصائح التوفير.",
    "Your energy report by Gmail, WhatsApp or SMS: today, tomorrow's forecast, the bill and tips.":
        "تقرير الطاقة الخاص بك عبر Gmail أو واتساب أو رسالة SMS: اليوم، وتوقع الغد، والفاتورة، والنصائح.",
    "Explore consumption by household, date and day type.": "استكشف الاستهلاك حسب المنزل والتاريخ ونوع اليوم.",
    "Where the energy goes inside the house (UCI sub-meters).": "أين تذهب الطاقة داخل المنزل (العدادات الفرعية في بيانات UCI).",
    "Random Forest vs XGBoost vs a 'same hour yesterday' baseline, and the next 24 hours.":
        "مقارنة Random Forest و XGBoost مع نموذج أساسي (نفس الساعة أمس)، وتوقع الـ24 ساعة القادمة.",
    "Hours the model did not expect, found on data it never trained on.": "ساعات لم يتوقعها النموذج، على بيانات لم يتدرب عليها.",
    "Hours above the consumption limit, and anomaly alerts.": "الساعات التي تجاوزت حد الاستهلاك، وتنبيهات القراءات غير الطبيعية.",
    "Simulated real-time monitoring.": "مراقبة لحظية (محاكاة).",
    "Live status of the Airflow pipeline, from the Airflow REST API.": "حالة خط بيانات Airflow الآن، من واجهة Airflow REST API.",
    "How the project works, the data it uses and what is done.": "كيف يعمل المشروع، والبيانات المستخدمة، وما تم إنجازه.",
    "This page is in English for now.": "هذه الصفحة متاحة بالإنجليزية حاليًا.",
    "Data: {source}": "البيانات: {source}",
    "admin": "مسؤول",
    "viewer": "مشاهد",
    "Log out": "تسجيل الخروج",
    "Light / dark mode: ⋮ menu (top right) → Light or Dark.": "الوضع الفاتح / الداكن: قائمة ⋮ (أعلى الصفحة) ← Light أو Dark.",
    "Could not train the models for this selection: {e}": "تعذّر تدريب النماذج لهذا الاختيار: {e}",
    "EGP": "جنيه",
    "kWh": "ك.و.س",

    # ---- login page (auth.py)
    "Smart-meter electricity pipeline - graduation project": "منصة لتحليل استهلاك الكهرباء من العدادات الذكية - مشروع تخرج",
    "Sign in with Google": "الدخول بحساب Google",
    "Anyone with a Google account can sign in.": "يمكن لأي شخص لديه حساب Google الدخول.",
    "**or** use a username and password": "**أو** استخدم اسم مستخدم وكلمة مرور",
    "Log in": "تسجيل الدخول",
    "Create account": "إنشاء حساب",
    "Username": "اسم المستخدم",
    "Password": "كلمة المرور",
    "Too many wrong tries. Wait a minute, then try again.": "محاولات خاطئة كثيرة. انتظر دقيقة ثم حاول مرة أخرى.",
    "Wrong username or password.": "اسم المستخدم أو كلمة المرور غير صحيحة.",
    "Choose a username": "اختر اسم مستخدم",
    "3 to 20 letters, digits, _ or -": "من 3 إلى 20 حرفًا إنجليزيًا أو رقمًا أو _ أو -",
    "Choose a password": "اختر كلمة مرور",
    "At least {n} characters": "{n} أحرف على الأقل",
    "Repeat the password": "أعد كتابة كلمة المرور",
    "Your password is stored hashed, never as plain text. New accounts can view every page except the admin tools. "
    "On the free online version, new accounts may be reset when the server restarts.":
        "تُحفظ كلمة المرور مشفّرة ولا تُحفظ أبدًا كنص عادي. يمكن للحسابات الجديدة رؤية كل الصفحات ما عدا أدوات المسؤول. "
        "في النسخة المجانية على الإنترنت قد تُحذف الحسابات الجديدة عند إعادة تشغيل الخادم.",
    "Your password is stored hashed, never as plain text. New accounts can view every page except the admin tools.":
        "تُحفظ كلمة المرور مشفّرة ولا تُحفظ أبدًا كنص عادي. يمكن للحسابات الجديدة رؤية كل الصفحات ما عدا أدوات المسؤول.",
    "Too many new accounts from this browser. Please use one of the accounts you created.":
        "تم إنشاء حسابات كثيرة من هذا المتصفح. من فضلك استخدم أحد الحسابات التي أنشأتها.",
    "Username: 3 to 20 letters, digits, _ or -.": "اسم المستخدم: من 3 إلى 20 حرفًا إنجليزيًا أو رقمًا أو _ أو -.",
    "Password: at least 8 characters.": "كلمة المرور: 8 أحرف على الأقل.",
    "The two passwords are different.": "كلمتا المرور غير متطابقتين.",
    "The password must not be the same as the username.": "يجب ألا تكون كلمة المرور هي نفس اسم المستخدم.",
    "This username is taken. Please choose another one.": "اسم المستخدم مستخدم بالفعل. من فضلك اختر اسمًا آخر.",
    "Could not reach the database. Please try again.": "تعذّر الاتصال بقاعدة البيانات. حاول مرة أخرى.",
    "The database refused the new account. Please try again later.": "رفضت قاعدة البيانات الحساب الجديد. حاول لاحقًا.",
    "The database refused to save your choice. Please try again later.": "رفضت قاعدة البيانات حفظ اختيارك. حاول لاحقًا.",

    # ---- Home
    "Smart-meter electricity pipeline: Azure Data Lake → Spark → Airflow → Snowflake → this dashboard, "
    "with machine learning, alerts and live monitoring on top.":
        "خط بيانات لاستهلاك الكهرباء من العدادات الذكية: تمر البيانات على Azure Data Lake ثم Spark ثم Airflow ثم "
        "Snowflake ثم هذه اللوحة، مع تعلّم الآلة والتنبيهات والمراقبة المباشرة.",
    "Data source": "مصدر البيانات",
    "connected": "متصل",
    "not connected": "غير متصل",
    "Airflow pipeline": "خط بيانات Airflow",
    "The data": "البيانات",
    "Total kWh": "إجمالي الاستهلاك (ك.و.س)",
    "Households": "عدد المنازل",
    "Years": "السنوات",
    "{a} to {b}": "من {a} إلى {b}",
    "Readings": "القراءات",
    "Half-hour readings": "قراءة كل نصف ساعة",
    "Best model": "أفضل نموذج",
    "{x}% vs baseline": "أفضل من النموذج الأساسي بـ{x}%",
    "Lower forecast error (RMSE)": "خطأ تنبؤ أقل (RMSE)",
    "Forecast error on the unseen hours": "خطأ التنبؤ على ساعات لم يرها النموذج",
    "In the unseen (test) hours": "في ساعات الاختبار",
    "Household: {h}. Change it, and the sensitivity, on the ML pages.": "المنزل: {h}. يمكن تغييره، وتغيير الحساسية، من صفحات تعلّم الآلة.",
    "This month's bill": "فاتورة هذا الشهر",
    "Expected for {month}": "المتوقع لشهر {month}",
    "{x} EGP vs budget": "{x} جنيه مقارنة بالميزانية",
    "Open Bill & Budget for details and saving tips": "افتح الفاتورة والميزانية للتفاصيل ونصائح التوفير",
    "Where to start": "ابدأ من هنا",
    "Watch the live monitor": "شاهد المراقبة المباشرة",
    "See the forecast": "شاهد التنبؤ",
    "Check your bill": "راجع فاتورتك",
    "How it works": "كيف يعمل المشروع",
    "How to use this dashboard": "طريقة استخدام اللوحة",
    "HOW_TO": "1. اختر صفحة من القائمة الجانبية.\n"
              "2. في صفحات تعلّم الآلة (التنبؤ، القراءات غير الطبيعية، التنبيهات، المراقبة المباشرة) اختر المنزل من القائمة الجانبية.\n"
              "3. في **المراقبة المباشرة** اضغط **Start** لإعادة تشغيل قراءات العداد، و**Spike** يضيف 3+ ك.و.س لإظهار تنبيه.\n"
              "4. الرسوم البيانية: اسحب للتحريك، و**Ctrl + عجلة الفأرة** للتكبير، و**Reset zoom** للرجوع.\n"
              "5. الوضع الفاتح / الداكن: قائمة **⋮** (أعلى الصفحة) ← **Light** أو **Dark**.",

    # ---- Bill & Budget
    "Tariff": "التعريفة",
    "Egyptian tiers (Aug 2026)": "شرائح مصر (أغسطس 2026)",
    "Fixed price per kWh": "سعر ثابت لكل ك.و.س",
    "Monthly budget (EGP)": "الميزانية الشهرية (جنيه)",
    "Today (live simulation)": "اليوم (محاكاة مباشرة)",
    "On: the meter readings are replayed on today's date and the model forecasts the rest. "
    "Off: pick a day in the historical data.":
        "تشغيل: تُعاد قراءات العداد على تاريخ اليوم ويتنبأ النموذج بالباقي. إيقاف: اختر يومًا من البيانات التاريخية.",
    "Month of (as of this day)": "الشهر (حتى هذا اليوم)",
    "The data is historical, so pick the day that counts as 'today'.": "البيانات تاريخية، فاختر اليوم الذي يُعتبر «اليوم».",
    "Price (EGP per kWh)": "السعر (جنيه لكل ك.و.س)",
    "Tariff tiers (EGP per kWh) - edit them if the government changes the prices":
        "شرائح التعريفة (جنيه لكل ك.و.س) - عدّلها إذا غيّرت الحكومة الأسعار",
    "This month so far": "هذا الشهر حتى الآن",
    "Expected at month end": "المتوقع في نهاية الشهر",
    "The range shows how much your daily use varied in the last 28 days.": "يوضح المدى مقدار تغيّر استهلاكك اليومي في آخر 28 يومًا.",
    "Last month": "الشهر الماضي",
    "{x} this month": "{x} هذا الشهر",
    "Not enough data for last month.": "لا توجد بيانات كافية عن الشهر الماضي.",
    "Budget": "الميزانية",
    "{x} EGP left": "متبقي {x} جنيه",
    "{x} EGP over": "زيادة {x} جنيه",
    "Budget alert: the expected bill ({a} EGP) is over your budget of {b} EGP.":
        "تنبيه الميزانية: الفاتورة المتوقعة ({a} جنيه) أعلى من ميزانيتك ({b} جنيه).",
    "On track: the expected bill is {x} EGP under your budget.": "أنت على المسار الصحيح: الفاتورة المتوقعة أقل من ميزانيتك بـ{x} جنيه.",
    "Experimental forecast: only {n} days of history before this day, so the month-end value is less certain.":
        "تنبؤ تجريبي: يوجد {n} يومًا فقط من البيانات قبل هذا اليوم، لذلك قيمة نهاية الشهر أقل دقة.",
    "{month}, as of {day}: {n} days left. Estimate only: each kWh is priced at its tier's price; real bills can add "
    "fees. Tomorrow uses the model's 24-hour forecast, the other days your average day.":
        "{month}، حتى {day}: متبقي {n} يومًا. تقدير فقط: كل ك.و.س بسعر شريحته، والفاتورة الحقيقية قد تضيف رسومًا. "
        "يوم الغد من تنبؤ النموذج لـ24 ساعة، وباقي الأيام بمتوسط يومك.",
    "🌍 **Carbon footprint:** about **{m} kg CO₂** this month ({u} kg so far). Absorbing it would take about "
    "**{t} trees** a year.":
        "🌍 **البصمة الكربونية:** حوالي **{m} كجم CO₂** هذا الشهر ({u} كجم حتى الآن). امتصاصها يحتاج حوالي "
        "**{t} شجرة** لمدة عام.",
    "Estimate: {f} kg CO₂ per kWh for Egypt's mostly natural-gas power, one tree absorbs about {t} kg CO₂ a year.":
        "تقدير: {f} كجم CO₂ لكل ك.و.س لكهرباء مصر المعتمدة غالبًا على الغاز الطبيعي، والشجرة تمتص حوالي {t} كجم CO₂ في العام.",
    "Bill through the month": "الفاتورة على مدار الشهر",
    "actual": "فعلي",
    "expected": "متوقع",
    "Red dashed line = your budget.": "الخط الأحمر المتقطع = ميزانيتك.",
    "Last 12 months": "آخر 12 شهرًا",
    "Where is my electricity going?": "أين تذهب الكهرباء؟",
    "Appliance (measured)": "الجهاز (مقاس)",
    "kWh this month": "ك.و.س هذا الشهر",
    "Share": "النسبة",
    "Cost (EGP)": "التكلفة (جنيه)",
    "Kitchen": "المطبخ",
    "Laundry": "الغسيل",
    "Water heater & AC": "السخان والتكييف",
    "Other (not sub-metered)": "أخرى (غير مقاسة)",
    "Measured by the house's three sub-meters; 'Other' is everything they do not cover.":
        "مقاسة بالعدادات الفرعية الثلاثة للمنزل؛ «أخرى» هي كل ما لا تغطيه.",
    "**Estimate for your own devices** - tick what you have and set the power and hours "
    "(a single meter cannot measure each device, so these are estimates):":
        "**تقدير لأجهزتك** - اختر الأجهزة الموجودة عندك وحدد القدرة وعدد الساعات "
        "(العداد الواحد لا يقيس كل جهاز، لذلك هذه تقديرات):",
    "Device": "الجهاز",
    "Have it": "موجود",
    "Power (W)": "القدرة (وات)",
    "Hours per day": "ساعات في اليوم",
    "kWh per month": "ك.و.س شهريًا",
    "Air conditioner (1.5 HP)": "تكييف (1.5 حصان)",
    "Refrigerator": "ثلاجة",
    "Electric water heater": "سخان كهربائي",
    "Washing machine": "غسالة",
    "Television": "تلفزيون",
    "Lighting (all LED)": "إضاءة (LED)",
    "Computer / laptop": "كمبيوتر / لابتوب",
    "Your devices add up to about {a} kWh a month; the meter expects {b} kWh for this month.":
        "مجموع أجهزتك حوالي {a} ك.و.س في الشهر؛ والعداد يتوقع {b} ك.و.س لهذا الشهر.",
    "Energy-saving assistant": "مساعد توفير الطاقة",
    "Tips are worked out from your own consumption, forecast and tariff.": "النصائح محسوبة من استهلاكك وتنبؤك وتعريفتك.",

    # ---- Reports page
    "How often": "كل كم؟",
    "Daily": "يومي",
    "Weekly": "أسبوعي",
    "Monthly": "شهري",
    "Daily: every evening at about 8 PM (Cairo time). Weekly: Saturdays. Monthly: the 1st of the month.":
        "يومي: كل مساء حوالي الساعة 8 (بتوقيت القاهرة). أسبوعي: كل سبت. شهري: أول كل شهر.",
    "Send it by": "أرسله عبر",
    "WhatsApp": "واتساب",
    "SMS": "رسالة SMS",
    "Report language": "لغة التقرير",
    "Gmail address": "عنوان Gmail",
    "Phone (with country code)": "رقم الهاتف (مع كود الدولة)",
    "WhatsApp key (CallMeBot)": "مفتاح واتساب (CallMeBot)",
    "CALLMEBOT_HELP": "رسائل واتساب مجانية عبر CallMeBot. مرة واحدة فقط: احفظ الرقم ‎+34 644 71 81 99‎ في جهات الاتصال وأرسل له "
                      "على واتساب الرسالة 'I allow callmebot to send me messages'. سيرد البوت بمفتاحك؛ الصقه هنا.",
    "Could not build the report: {e}": "تعذّر إنشاء التقرير: {e}",
    "Today (expected total)": "اليوم (الإجمالي المتوقع)",
    "Tomorrow (forecast)": "الغد (تنبؤ)",
    "{x} vs today ({trend})": "{x} مقارنة باليوم ({trend})",
    "higher": "أعلى",
    "lower": "أقل",
    "about the same": "تقريبًا نفس الاستهلاك",
    "Forecast by the trained model for tomorrow, 00:00 to 24:00.": "تنبؤ النموذج المدرَّب ليوم الغد من 00:00 إلى 24:00.",
    "{month} bill (expected)": "فاتورة {month} (متوقعة)",
    "Budget and tariff come from the Bill & Budget page.": "الميزانية والتعريفة من صفحة الفاتورة والميزانية.",
    "Readings until {time} today ({date}); the model ({model}) forecasts the rest of today and tomorrow.":
        "القراءات حتى {time} اليوم ({date})؛ والنموذج ({model}) يتنبأ بباقي اليوم والغد.",
    "Preview": "معاينة",
    "{n} characters ≈ {m} SMS.": "{n} حرفًا ≈ {m} رسالة SMS.",
    "Subject": "الموضوع",
    "Send me one now": "أرسل لي تقريرًا الآن",
    "Save my choice": "احفظ اختياري",
    "You can send {n} test reports per visit. Log in again to send more.": "يمكنك إرسال {n} تقارير تجريبية في كل زيارة. سجّل الدخول مرة أخرى لإرسال المزيد.",
    "Sending...": "جارٍ الإرسال...",
    "Saved: {freq} report by {channels}.": "تم الحفظ: تقرير {freq} عبر {channels}.",
    "Automatic sending: GitHub Actions sends the reports every evening at about 8 PM (Cairo time) to everyone who "
    "saved a choice. Choices are kept in the database.":
        "الإرسال التلقائي: يرسل GitHub Actions التقارير كل مساء حوالي الساعة 8 (بتوقيت القاهرة) لكل من حفظ اختياره. "
        "الاختيارات محفوظة في قاعدة البيانات.",
    "Automatic sending: GitHub Actions sends the reports every evening at about 8 PM (Cairo time). On this version the "
    "admin adds saved choices to the schedule list. Saved choices may be reset when the online server restarts.":
        "الإرسال التلقائي: يرسل GitHub Actions التقارير كل مساء حوالي الساعة 8 (بتوقيت القاهرة). في هذه النسخة يضيف "
        "المسؤول الاختيارات المحفوظة إلى قائمة الإرسال، وقد تُحذف عند إعادة تشغيل الخادم.",
    "Schedule list (admin only: it has phone numbers and keys)": "قائمة الإرسال (للمسؤول فقط: فيها أرقام هواتف ومفاتيح)",
    "Kept in the Supabase database: GitHub reads them by itself, nothing to copy.": "محفوظة في قاعدة بيانات Supabase: يقرأها GitHub تلقائيًا، لا حاجة للنسخ.",
    "Copy all of it into the GitHub secret REPORT_SUBSCRIBERS (repo → Settings → Secrets and variables → Actions); "
    "GitHub then sends these reports on schedule.":
        "انسخها كلها في سر GitHub باسم REPORT_SUBSCRIBERS (المستودع ← Settings ← Secrets and variables ← Actions)؛ "
        "وبعدها يرسل GitHub هذه التقارير في موعدها.",
    "Nobody has saved a choice yet.": "لم يحفظ أحد اختياره بعد.",
}
