/*
  Aura Retail marketing site -- bilingual content.

  Every string a visitor can read lives here, keyed by a dotted path that
  matches a `data-i18n="..."` attribute in the HTML (see js/i18n.js). Arabic
  is written as Arabic, not translated word-for-word from the English --
  see website/README.md for how to edit either language safely.

  PRICING NUMBERS live in one place: the PRICING block near the top of
  this file, right below the CONTACT block. That is the only place to
  change a figure. Pricing is a ONE-TIME purchase, decided 2026-09-23 --
  a shop pays once per licence, never a subscription or a renewal. See
  that decision reflected in `pricing_page` and `pricing_teaser` below,
  and in website/README.md's "Where the pricing numbers live".

  CONTACT DETAILS (email/phone) also live in one place: the CONTACT
  block immediately below this comment. See its own comment for exactly
  how it is wired to the rest of the site -- do not hardcode a second
  copy of the email or phone anywhere else.

  Product terminology (branch/فرع, supplier/المورد, quotation/عرض سعر,
  barcode scanner/قارئ الباركود, offline grace period/فترة سماح دون اتصال,
  trial balance/ميزان المراجعة, profit and loss/الأرباح والخسائر, balance
  sheet/الميزانية العمومية, loyalty points/نقاط الولاء, stock transfers/
  تحويلات المخزون, licence/الترخيص) is taken verbatim from
  products/retail/frontend/locales/ar.json so this site reads as the same
  product a shopkeeper will actually use, not a marketing dialect of it.
*/
(function (global) {
  "use strict";

  // ============================================================
  // CONTACT DETAILS -- edit ONLY here. Nowhere else on the site
  // hardcodes a second copy.
  // ============================================================
  // `email` is a PLACEHOLDER: Action Aura has no official project email
  // live yet (one is being set up, as of 2026-09-23). Change the string
  // below and it updates everywhere automatically -- the footer on all
  // four pages, the "Prefer to reach us directly?" panel on
  // contact.html, and the contact form's own mailto: submission in
  // js/main.js -- every one of those reads THIS value, either directly
  // (`window.AURA_CONTACT.email` in js/main.js) or via an element
  // carrying `data-contact-link="email"` (populated by
  // `initContactLinks()` in js/main.js). There is nothing else to hunt
  // for.
  //
  // `phone` is deliberately EMPTY. There is no real phone/WhatsApp
  // number yet, and a fake-looking one on a sales site is worse than
  // none, so nothing on the site currently displays a phone number.
  // Once a real one exists: put it here (digits and a leading "+", e.g.
  // "+962791234567"), then add a `data-contact-link="phone"` element
  // where it should appear -- js/main.js's `initContactLinks()` already
  // knows how to wire a "phone" link the same way it wires "email".
  // The domain IS registered: actionaura.me (Namecheap, through Aug 2027).
  // `info@` rather than `hello@` -- a shopkeeper deciding whether to trust a
  // till with his money reads `info@` as a business and `hello@` as a
  // side project.
  //
  // STILL A PLACEHOLDER UNTIL THE MAILBOX EXISTS. The domain is owned but no
  // mail is configured on it yet, so this address does not receive anything
  // today. Set up a mailbox (Zoho Mail's free tier takes a custom domain, or
  // Google Workspace if you want it paid and familiar), send yourself a test
  // message, and only then treat this line as live. An address printed on a
  // sales page that silently drops enquiries is worse than no address --
  // the visitor believes they contacted you.
  var CONTACT = {
    email: "info@actionaura.me",
    phone: ""
  };

  var PRICING = {
    currency_en: "JD",
    currency_ar: "د.أ",
    base_price: "250",
    base_devices: "2",
    extra_device_price: "50",
    extra_branch_price: "250"
  };

  var content = {
    en: {
      meta: {
        home_title: "Aura Retail — the till that keeps working when the internet doesn't",
        home_desc: "Arabic-first point of sale for Jordanian shops. Real double-entry accounting, offline-first selling, and one licence for your till and your phone — paid once, not a subscription.",
        features_title: "Features — Aura Retail",
        features_desc: "Barcode scanning, receipt printing, real accounting, multi-branch stock transfers, loyalty points and multi-device sync, built for shops that can't always rely on the internet.",
        pricing_title: "Pricing — Aura Retail",
        pricing_desc: "One licence covers two devices, paid once — not a subscription. See exactly what a shop pays to run Aura Retail.",
        contact_title: "Request a demo — Aura Retail",
        contact_desc: "Talk to us about bringing Aura Retail to your shop."
      },
      nav: { home: "Home", features: "Features", pricing: "Pricing", contact: "Contact", cta: "Request a demo" },
      brand: { line2: "RETAIL" },
      features_page: {
        eyebrow: "Everything, in detail",
        h1: "What actually runs on the counter.",
        lede: "Not a feature list of promises — this is what ships in the app your cashier opens every morning, grouped the way a shop actually uses it."
      },
      extras_group1: "Selling and hardware",
      extras_group2: "Growing beyond one till",
      extras_group3: "Money owed, and money you owe",
      hero: {
        eyebrow: "Point of sale, built for Jordan",
        h1: "The till that doesn't stop when the internet does.",
        lede: "Aura Retail keeps ringing up sales, printing receipts and opening the cash drawer even when your connection drops — with a full Arabic-first interface, real double-entry accounting, and one licence that covers your till and your phone.",
        cta_primary: "See what it does",
        cta_secondary: "Request a demo",
        fine_print: "No internet required to keep selling — a 14-day offline grace period, by design."
      },
      till: {
        label: "TILL 1 · MAIN BRANCH",
        status: "No internet connection",
        item1: "T-shirt, size M", item1_amt: "8.500",
        item2: "Phone case", item2_amt: "3.250",
        item3: "Chewing gum ×3", item3_amt: "1.500",
        total_label: "Total", total_amt: "13.250",
        foot: "Saved on this device — syncs automatically once the connection returns."
      },
      hero_stats: [
        { n: "14", label: "days offline before any warning" },
        { n: "2", label: "devices on one licence" },
        { n: "40", label: "double-entry ledger accounts, out of the box" },
        { n: "2", label: "platforms — Windows and Android" }
      ],
      proof: [
        { title: "Keeps selling offline", desc: "14-day grace period, warn-only by design — never a hard stop at the counter." },
        { title: "Real double-entry accounting", desc: "A general ledger, trial balance, profit & loss and balance sheet — not just a sales log." },
        { title: "One licence, every device", desc: "A till and a phone sync over your shop's own wifi — no cloud required." },
        { title: "Arabic first", desc: "A full right-to-left interface your staff can learn in an afternoon. English too." }
      ],
      pillars: {
        offline: {
          eyebrow: "Built for Jordan's internet",
          title: "Sells first. Asks questions later.",
          desc: "The connection can drop for a day, a week, or longer — Aura Retail keeps selling regardless. When it does come back, everything catches up on its own.",
          link: "How offline selling works"
        },
        accounting: {
          title: "Real accounting",
          desc: "A general ledger under every sale, purchase and return — not a spreadsheet pretending to be one."
        },
        sync: {
          title: "Multi-device sync",
          desc: "The manager's phone and the counter till agree with each other automatically, over the shop's own wifi."
        },
        hardware: {
          title: "Hardware ready",
          desc: "Real barcode scanners, receipt printers and cash drawers — plug in and go, no special setup."
        },
        arabic: {
          title: "Arabic-first interface",
          desc: "Every screen mirrors correctly, right to left, with English available at a tap."
        }
      },
      deepdive: {
        offline: {
          eyebrow: "No internet, no problem",
          h3: "The counter never has to stop.",
          lede: "Jordanian shop wifi drops. Aura Retail is built around that fact instead of pretending it won't happen.",
          points: [
            "A 14-day offline grace period before any warning appears — and the policy is warn-only, so a lapsed connection never blocks a sale.",
            "Devices in the same shop find and sync with each other over your own wifi network, with no internet and no cloud required.",
            "Barcode scanning, receipt printing and the cash drawer all work exactly the same with the connection up or down.",
            "The moment the connection comes back, every sale, return and stock movement catches up automatically."
          ],
          timeline_now: "Today", timeline_grace: "14-day grace period", timeline_warn: "Warning shown — selling continues", timeline_note: "The counter is never locked out by a clock."
        },
        accounting: {
          eyebrow: "Not just a sales log",
          h3: "A real set of books, not a spreadsheet.",
          lede: "Every sale, purchase, return and stock transfer posts a balanced entry to a genuine double-entry general ledger — the same one an accountant would recognise.",
          points: [
            "A trial balance, a profit & loss statement and a balance sheet, generated straight from the ledger.",
            "Cost of goods sold, tracked automatically against a moving average cost per branch.",
            "A fixed chart of accounts you can trust — every posted entry balances, enforced by the database itself, not by hope.",
            "Corrections post a reversing entry rather than silently editing history, exactly like a real set of books should."
          ],
          ledger_title: "Trial balance — sample", ledger_period: "This month",
          ledger_rows: [
            ["Cash and Bank", "3,420.750"],
            ["Accounts Receivable", "612.400"],
            ["Inventory", "8,940.000"],
            ["Sales Revenue", "6,180.500"],
            ["Cost of Goods Sold", "3,715.250"]
          ],
          ledger_total_label: "Debits = Credits", ledger_total: "Balanced"
        },
        sync: {
          eyebrow: "One shop, every device",
          h3: "The phone in your pocket and the till on the counter, in step.",
          lede: "One licence covers two devices — a manager's phone and a cashier's till are the usual pairing. Add more devices or another branch as the shop grows.",
          points: [
            "Devices discover each other automatically on the shop's wifi — nothing to type, no IP address, no pairing code.",
            "A sale rung up on the phone shows up on the till within seconds, and the other way around.",
            "Membership is decided by the licence alone, so a device can't join a shop it wasn't licensed for.",
            "One device in the shop hosts quietly in the background — never the phone that's about to leave the building."
          ],
          device1: "Manager's phone", device2: "Counter till"
        }
      },
      einvoicing: {
        title: "E-invoicing (JoFotara / ISTD) — built, not yet switched on",
        p1: "The full pipeline for Jordan's e-invoicing mandate is already built: UBL 2.1 XML generation, QR codes, a dedicated gapless invoice sequence, a retry-with-backoff outbox and a full audit trail.",
        p2: "What it does not yet do is submit to the tax authority — that step is waiting on our ISTD portal registration, not on engineering work. We will not tell you Aura Retail is e-invoicing compliant until it actually is. When registration completes, switching submission on is a configuration change, not a rebuild."
      },
      extras_title: "Everything else a real shop needs",
      extras_lede: "Beyond the till itself.",
      extras: [
        { icon: "icon-barcode", title: "Barcode scanning", desc: "Works with real handheld and counter-mounted hardware scanners — plug and scan." },
        { icon: "icon-printer", title: "Receipt printing", desc: "Thermal receipt printers, ready out of the box, in Arabic and English." },
        { icon: "icon-drawer", title: "Cash drawer", desc: "The drawer pops open automatically at the end of a sale — no extra button to remember." },
        { icon: "icon-branch", title: "Multi-branch", desc: "Run more than one location from the same account, each with its own stock and staff." },
        { icon: "icon-transfer", title: "Stock transfers", desc: "Move stock between branches with a proper in-transit and received workflow, not a guess." },
        { icon: "icon-people", title: "Customer accounts", desc: "Track balances owed, statements and payments for customers who run a tab." },
        { icon: "icon-supplier", title: "Supplier payables", desc: "Track what the shop owes suppliers, with ageing and payment history." },
        { icon: "icon-loyalty", title: "Loyalty points", desc: "Customers earn points on purchases and spend them at the till, on the spot." },
        { icon: "icon-quote", title: "Quotations", desc: "Turn a price quote into a sale with one tap once the customer says yes." }
      ],
      requirements_title: "What you need to run it",
      requirements_lede: "No server, no IT department.",
      requirements: [
        { icon: "icon-laptop", title: "A Windows PC", desc: "Any everyday Windows laptop or desktop at the counter — the app installs like any other program." },
        { icon: "icon-phone", title: "An Android phone", desc: "A mid-range Android phone works for the owner or manager's device." },
        { icon: "icon-sync", title: "The shop's own wifi", desc: "For multi-device sync — no cloud account, no server bill, no IT visit." }
      ],
      steps_title: "Getting started",
      steps: [
        { title: "Get a licence", desc: "One licence covers your till and a manager's phone, priced for a single shop." },
        { title: "Install and sign in", desc: "Install on Windows and Android and sign in — a second device joins with the licence alone." },
        { title: "Start selling", desc: "With the internet up or down. Everything else — accounting, stock, reports — follows from there." }
      ],
      pricing_teaser: {
        eyebrow: "Pricing — paid once",
        title: "Straightforward, by the device.",
        desc: "One licence, one shop, two devices included — paid once, not a subscription. Add a device or a branch only when you need one, also paid once.",
        cta: "See full pricing"
      },
      pricing_page: {
        eyebrow: "Pricing",
        h1: "One licence per shop. Add only what you grow into.",
        lede: "Prices are in Jordanian dinar, paid once. There is no subscription, no monthly fee, and nothing that renews.",
        plan_title: "Shop licence",
        plan_desc: "Everything a single shop needs to run its counter and its books.",
        plan_per: "one-time",
        plan_features: [
          "Paid once — no subscription, no monthly fee, ever",
          "2 devices included — a till and a manager's phone",
          "Full Arabic and English interface",
          "Barcode scanning, receipt printing, cash drawer",
          "Real double-entry accounting: ledger, trial balance, P&L, balance sheet",
          "Customer accounts, supplier payables, loyalty points, quotations",
          "Offline selling with a 14-day grace period",
          "Multi-device sync over the shop's own wifi"
        ],
        plan_cta: "Request this licence",
        multi_title: "Growing to more branches?",
        multi_desc: "Add branches and extra devices as the shop grows — each is a one-time addition, priced in the table below.",
        multi_cta: "Talk to us",
        addon_title: "Add-ons",
        addon_col_desc: "What it adds",
        addon_col_price: "Price",
        addon_device_label: "Extra device (till or phone) beyond the 2 included",
        addon_device_unit: "/ device",
        addon_branch_label: "Extra branch — its own stock, staff and till",
        addon_branch_unit: "/ branch",
        note: "Extra devices and branches are added to your licence the same way — a one-time payment, not a recurring charge."
      },
      contact_page: {
        eyebrow: "Contact",
        h1: "Tell us about your shop.",
        lede: "We'll reply with next steps and, if useful, set up a short demo at a time that works for you.",
        form_name: "Your name",
        form_shop: "Shop name",
        form_city: "City",
        form_phone: "Phone or WhatsApp",
        form_email: "Email",
        form_message: "Anything we should know? (number of branches, current system, etc.)",
        form_submit: "Send via email",
        form_note: "This site has no server behind it. Sending opens your email app with this message pre-filled, addressed to us — nothing is uploaded anywhere.",
        alt_title: "Prefer to reach us directly?",
        alt_email_label: "Email",
        subject: "Demo request — Aura Retail"
      },
      footer: {
        desc: "Arabic-first point of sale for Jordanian shops. Built to keep selling when the internet doesn't.",
        product_h: "Product",
        company_h: "Get in touch",
        note_h: "Good to know",
        note1: "Prices shown in JD, paid once — not a subscription.",
        note2: "E-invoicing is built but not yet switched on — see the Features page.",
        note3: "No cloud account required for sync between your own devices.",
        rights: "Action Aura. All rights reserved."
      }
    },
    ar: {
      meta: {
        home_title: "أورا ريتيل — نظام كاشير يواصل العمل حتى بلا إنترنت",
        home_desc: "نظام نقاط بيع عربي أولاً للمحلات الأردنية. محاسبة قيد مزدوج حقيقية، وبيع لا يتوقف بانقطاع الإنترنت، وترخيص واحد لجهاز الكاشير والهاتف — يُدفع مرة واحدة فقط، وليس اشتراكاً.",
        features_title: "المزايا — أورا ريتيل",
        features_desc: "قارئ باركود، طباعة إيصالات، محاسبة حقيقية، تحويلات مخزون بين الفروع، نقاط ولاء، ومزامنة بين الأجهزة — لمحلات لا تضمن اتصالاً دائماً بالإنترنت.",
        pricing_title: "الأسعار — أورا ريتيل",
        pricing_desc: "ترخيص واحد يغطي جهازين، يُدفع مرة واحدة فقط — وليس اشتراكاً. اطّلع بدقة على ما يدفعه المحل لتشغيل أورا ريتيل.",
        contact_title: "اطلب عرضاً تجريبياً — أورا ريتيل",
        contact_desc: "تواصل معنا لتشغيل أورا ريتيل في محلك."
      },
      nav: { home: "الرئيسية", features: "المزايا", pricing: "الأسعار", contact: "تواصل معنا", cta: "اطلب عرضاً تجريبياً" },
      brand: { line2: "ريتيل" },
      features_page: {
        eyebrow: "كل التفاصيل",
        h1: "ما يعمل فعلاً على الصندوق.",
        lede: "لا قائمة وعود — هذا بالضبط ما يفتحه الكاشير كل صباح، مرتّباً بالطريقة التي يستخدمه بها محل حقيقي."
      },
      extras_group1: "البيع والأجهزة",
      extras_group2: "التوسع إلى أكثر من صندوق",
      extras_group3: "ما لك وما عليك من أموال",
      hero: {
        eyebrow: "نظام نقاط بيع، مصمم للأردن",
        h1: "الكاشير الذي لا يتوقف حين ينقطع الإنترنت.",
        lede: "يواصل أورا ريتيل تسجيل المبيعات وطباعة الإيصالات وفتح درج النقد حتى عند انقطاع الاتصال — بواجهة عربية كاملة من الأساس، ومحاسبة قيد مزدوج حقيقية، وترخيص واحد يغطي جهاز الكاشير وهاتفك.",
        cta_primary: "شاهد ما يقدمه النظام",
        cta_secondary: "اطلب عرضاً تجريبياً",
        fine_print: "لا حاجة للإنترنت لمواصلة البيع — فترة سماح 14 يوماً دون اتصال، بالتصميم لا بالصدفة."
      },
      till: {
        label: "الكاشير 1 · الفرع الرئيسي",
        status: "لا يوجد اتصال بالإنترنت",
        item1: "قميص، مقاس M", item1_amt: "8.500",
        item2: "غطاء هاتف", item2_amt: "3.250",
        item3: "علكة × 3", item3_amt: "1.500",
        total_label: "الإجمالي", total_amt: "13.250",
        foot: "تم الحفظ على هذا الجهاز — وستتم المزامنة تلقائياً عند عودة الاتصال."
      },
      hero_stats: [
        { n: "14", label: "يوماً سماح دون إنترنت قبل أي تحذير" },
        { n: "2", label: "جهاز بترخيص واحد" },
        { n: "40", label: "حساباً في دفتر الأستاذ، جاهزة من أول تشغيل" },
        { n: "2", label: "نظامان — ويندوز وأندرويد" }
      ],
      proof: [
        { title: "يواصل البيع دون إنترنت", desc: "فترة سماح 14 يوماً، تحذيرية بالتصميم — لا توقف قسري عند الصندوق أبداً." },
        { title: "محاسبة قيد مزدوج حقيقية", desc: "دفتر أستاذ عام، ميزان مراجعة، أرباح وخسائر، وميزانية عمومية — لا مجرد سجل مبيعات." },
        { title: "ترخيص واحد لكل أجهزتك", desc: "الكاشير والهاتف يتزامنان عبر شبكة واي فاي محلك الخاصة — دون الحاجة لأي سحابة." },
        { title: "عربي أولاً", desc: "واجهة كاملة باتجاه اليمين لليسار يتعلمها فريقك خلال بعد ظهر واحد. والإنجليزية متاحة أيضاً." }
      ],
      pillars: {
        offline: {
          eyebrow: "مصمم لإنترنت الأردن",
          title: "يبيع أولاً. ويسأل لاحقاً.",
          desc: "قد ينقطع الاتصال ليوم أو أسبوع أو أكثر — يواصل أورا ريتيل البيع رغم ذلك. وحين يعود الاتصال، يلحق كل شيء بنفسه.",
          link: "كيف يعمل البيع دون إنترنت"
        },
        accounting: {
          title: "محاسبة حقيقية",
          desc: "دفتر أستاذ عام خلف كل عملية بيع وشراء وإرجاع — لا جدول بيانات يتظاهر بأنه نظام محاسبة."
        },
        sync: {
          title: "مزامنة بين الأجهزة",
          desc: "هاتف المدير وكاشير الصندوق يتفقان تلقائياً، عبر شبكة واي فاي المحل نفسها."
        },
        hardware: {
          title: "جاهز للأجهزة",
          desc: "قارئات باركود وطابعات إيصالات وأدراج نقد حقيقية — وصّلها وابدأ العمل، دون إعدادات معقدة."
        },
        arabic: {
          title: "واجهة عربية أولاً",
          desc: "كل شاشة تنعكس بشكل صحيح من اليمين لليسار، مع إمكانية التبديل للإنجليزية بضغطة واحدة."
        }
      },
      deepdive: {
        offline: {
          eyebrow: "بلا إنترنت، بلا مشكلة",
          h3: "الصندوق لا يتوقف أبداً.",
          lede: "شبكة الإنترنت في المحلات الأردنية تنقطع أحياناً. صُمم أورا ريتيل على هذا الأساس، لا على افتراض أن ذلك لن يحدث.",
          points: [
            "فترة سماح 14 يوماً دون اتصال قبل ظهور أي تحذير — والسياسة تحذيرية فقط، فانقطاع الاتصال لا يوقف عملية بيع أبداً.",
            "الأجهزة في المحل نفسه تتعرف على بعضها وتتزامن عبر شبكة الواي فاي الخاصة بك، دون إنترنت ودون أي سحابة.",
            "قارئ الباركود وطباعة الإيصالات ودرج النقد، جميعها تعمل بالطريقة نفسها سواء كان الاتصال متوفراً أو منقطعاً.",
            "بمجرد عودة الاتصال، تلحق كل عملية بيع وإرجاع وحركة مخزون بنفسها تلقائياً."
          ],
          timeline_now: "اليوم", timeline_grace: "فترة سماح 14 يوماً", timeline_warn: "يظهر تحذير — والبيع مستمر", timeline_note: "الصندوق لا يُقفل أبداً بسبب عدّاد زمني."
        },
        accounting: {
          eyebrow: "لا مجرد سجل مبيعات",
          h3: "دفاتر محاسبية حقيقية، لا جدول بيانات.",
          lede: "كل عملية بيع وشراء وإرجاع وتحويل مخزون تُسجَّل كقيد متوازن في دفتر أستاذ عام حقيقي بنظام القيد المزدوج — نفس الدفتر الذي يفهمه أي محاسب.",
          points: [
            "ميزان مراجعة، وقائمة أرباح وخسائر، وميزانية عمومية، جميعها تُبنى مباشرة من دفتر الأستاذ.",
            "تكلفة البضاعة المباعة، تُحتسب تلقائياً وفق متوسط تكلفة متحرك لكل فرع على حدة.",
            "شجرة حسابات ثابتة يمكن الوثوق بها — كل قيد مسجَّل متوازن، وتفرضه قاعدة البيانات نفسها لا مجرد الأمل بذلك.",
            "أي تصحيح يُسجَّل كقيد عكسي، لا كتعديل صامت للتاريخ — تماماً كما يجب أن تعمل الدفاتر الحقيقية."
          ],
          ledger_title: "ميزان المراجعة — نموذج", ledger_period: "هذا الشهر",
          ledger_rows: [
            ["النقدية والبنوك", "3,420.750"],
            ["ذمم العملاء المدينة", "612.400"],
            ["المخزون", "8,940.000"],
            ["إيرادات المبيعات", "6,180.500"],
            ["تكلفة البضاعة المباعة", "3,715.250"]
          ],
          ledger_total_label: "المدين = الدائن", ledger_total: "متوازن"
        },
        sync: {
          eyebrow: "متجر واحد، كل جهاز",
          h3: "الهاتف في جيبك، والكاشير على الصندوق، في تناغم دائم.",
          lede: "ترخيص واحد يغطي جهازين — هاتف المدير وكاشير الموظف هما التركيبة المعتادة. أضف أجهزة أو فرعاً آخر لاحقاً مع نمو المحل.",
          points: [
            "تتعرف الأجهزة على بعضها تلقائياً عبر واي فاي المحل — لا كتابة عنوان IP، ولا رمز اقتران.",
            "عملية بيع تُسجَّل من الهاتف تظهر على الكاشير خلال ثوانٍ، والعكس صحيح.",
            "العضوية في المحل يحددها الترخيص وحده، فلا يمكن لجهاز الانضمام إلى محل لم يُرخَّص له.",
            "جهاز واحد في المحل يستضيف الشبكة بهدوء في الخلفية — وليس الهاتف الذي على وشك مغادرة المحل أبداً."
          ],
          device1: "هاتف المدير", device2: "كاشير الصندوق"
        }
      },
      einvoicing: {
        title: "الفوترة الإلكترونية (جوفوترة / الضريبة) — جاهزة، ولم تُفعَّل بعد",
        p1: "المسار الكامل لمتطلبات الفوترة الإلكترونية الأردنية مبني بالفعل: توليد ملفات UBL 2.1، رموز QR، تسلسل فواتير متتابع دون فجوات، وصندوق إرسال يعيد المحاولة تلقائياً مع سجل تدقيق كامل.",
        p2: "ما لم يُنجز بعد هو الإرسال الفعلي لهيئة الضريبة — وهذه الخطوة تنتظر إتمام تسجيلنا في بوابة الضريبة، لا عملاً برمجياً. لن نخبرك أن أورا ريتيل متوافق مع الفوترة الإلكترونية قبل أن يكون كذلك فعلاً. وعند اكتمال التسجيل، سيكون تفعيل الإرسال تغيير إعداد بسيط، لا إعادة بناء."
      },
      extras_title: "كل ما يحتاجه محل حقيقي أيضاً",
      extras_lede: "وما وراء الصندوق نفسه.",
      extras: [
        { icon: "icon-barcode", title: "قارئ الباركود", desc: "يعمل مع قارئات الباركود الحقيقية، المحمولة والثابتة على الطاولة — وصّله وابدأ المسح." },
        { icon: "icon-printer", title: "طباعة الإيصالات", desc: "طابعات إيصالات حرارية، جاهزة من أول استخدام، بالعربية والإنجليزية." },
        { icon: "icon-drawer", title: "درج النقد", desc: "يُفتح درج النقد تلقائياً عند إتمام عملية البيع — دون زر إضافي يجب تذكره." },
        { icon: "icon-branch", title: "فروع متعددة", desc: "شغّل أكثر من فرع من الحساب نفسه، ولكل فرع مخزونه وموظفوه الخاصون." },
        { icon: "icon-transfer", title: "تحويلات المخزون", desc: "انقل البضاعة بين الفروع بسير عمل حقيقي: قيد النقل ثم الاستلام، لا مجرد تخمين." },
        { icon: "icon-people", title: "حسابات العملاء", desc: "تابع أرصدة العملاء وكشوف حساباتهم ودفعاتهم لمن يشتري بالدَّين." },
        { icon: "icon-supplier", title: "ذمم الموردين", desc: "تابع ما يدين به المحل للموردين، مع أعمار الديون وسجل الدفعات." },
        { icon: "icon-loyalty", title: "نقاط الولاء", desc: "يكسب العملاء نقاطاً عند الشراء وينفقونها عند الصندوق مباشرة." },
        { icon: "icon-quote", title: "عروض الأسعار", desc: "حوّل عرض السعر إلى عملية بيع بضغطة واحدة بمجرد موافقة العميل." }
      ],
      requirements_title: "ما تحتاجه لتشغيله",
      requirements_lede: "دون خادم، ودون قسم تقنية معلومات.",
      requirements: [
        { icon: "icon-laptop", title: "جهاز ويندوز", desc: "أي حاسوب محمول أو مكتبي عادي يعمل بويندوز على الصندوق — يُثبَّت البرنامج كأي برنامج آخر." },
        { icon: "icon-phone", title: "هاتف أندرويد", desc: "هاتف أندرويد متوسط الفئة يكفي لجهاز المالك أو المدير." },
        { icon: "icon-sync", title: "واي فاي المحل نفسه", desc: "للمزامنة بين الأجهزة — دون حساب سحابي، ودون فاتورة خادم، ودون زيارة فني." }
      ],
      steps_title: "خطوات البدء",
      steps: [
        { title: "احصل على ترخيص", desc: "ترخيص واحد يغطي كاشيرك وهاتف المدير، بسعر مخصص لمحل واحد." },
        { title: "ثبّت وسجّل الدخول", desc: "ثبّته على ويندوز وأندرويد وسجّل الدخول — وينضم الجهاز الثاني بالترخيص وحده." },
        { title: "ابدأ البيع", desc: "سواء توفر الإنترنت أم لا. وكل شيء آخر — المحاسبة، المخزون، التقارير — يتبع ذلك تلقائياً." }
      ],
      pricing_teaser: {
        eyebrow: "الأسعار — دفعة واحدة",
        title: "واضحة، بحسب الجهاز.",
        desc: "ترخيص واحد، لمحل واحد، مع جهازين ضمن السعر — يُدفع مرة واحدة فقط، لا كاشتراك. أضف جهازاً أو فرعاً فقط عند الحاجة إليه، وأيضاً بدفعة واحدة.",
        cta: "شاهد الأسعار كاملة"
      },
      pricing_page: {
        eyebrow: "الأسعار",
        h1: "ترخيص واحد لكل محل. أضف فقط ما تكبر إليه.",
        lede: "الأسعار بالدينار الأردني، وتُدفع دفعة واحدة. لا اشتراك، ولا رسوم شهرية، ولا أي شيء يتجدد.",
        plan_title: "ترخيص المحل",
        plan_desc: "كل ما يحتاجه محل واحد لتشغيل صندوقه ودفاتره.",
        plan_per: "دفعة واحدة",
        plan_features: [
          "دفعة واحدة فقط — بلا اشتراك وبلا رسوم شهرية أبداً",
          "جهازان ضمن السعر — كاشير وهاتف مدير",
          "واجهة كاملة بالعربية والإنجليزية",
          "قارئ باركود، طباعة إيصالات، درج نقد",
          "محاسبة قيد مزدوج حقيقية: دفتر أستاذ، ميزان مراجعة، أرباح وخسائر، ميزانية عمومية",
          "حسابات العملاء، ذمم الموردين، نقاط الولاء، عروض الأسعار",
          "بيع دون إنترنت بفترة سماح 14 يوماً",
          "مزامنة بين الأجهزة عبر واي فاي المحل"
        ],
        plan_cta: "اطلب هذا الترخيص",
        multi_title: "تتوسع لأكثر من فرع؟",
        multi_desc: "أضف فروعاً وأجهزة إضافية مع نمو المحل — كل إضافة تُدفع مرة واحدة، بالأسعار الموضحة في الجدول أدناه.",
        multi_cta: "تواصل معنا",
        addon_title: "الإضافات",
        addon_col_desc: "ما الذي تضيفه",
        addon_col_price: "السعر",
        addon_device_label: "جهاز إضافي (كاشير أو هاتف) بعد الجهازين المُضمَّنين",
        addon_device_unit: "/ جهاز",
        addon_branch_label: "فرع إضافي — بمخزونه وموظفيه وكاشيره الخاص",
        addon_branch_unit: "/ فرع",
        note: "الأجهزة والفروع الإضافية تُضاف إلى ترخيصك بالطريقة نفسها — دفعة واحدة، لا رسوم متكررة."
      },
      contact_page: {
        eyebrow: "تواصل معنا",
        h1: "حدّثنا عن محلك.",
        lede: "سنرد عليك بالخطوات التالية، ونرتب عرضاً تجريبياً قصيراً في الوقت المناسب لك إن أردت.",
        form_name: "اسمك",
        form_shop: "اسم المحل",
        form_city: "المدينة",
        form_phone: "رقم الهاتف أو واتساب",
        form_email: "البريد الإلكتروني",
        form_message: "أي تفاصيل تودّ إخبارنا بها؟ (عدد الفروع، النظام الحالي، إلخ)",
        form_submit: "إرسال عبر البريد الإلكتروني",
        form_note: "لا يوجد خادم خلف هذا الموقع. الإرسال يفتح برنامج بريدك الإلكتروني برسالة معبأة مسبقاً وموجهة إلينا — لا يُرفع شيء إلى أي مكان.",
        alt_title: "تفضّل التواصل المباشر؟",
        alt_email_label: "البريد الإلكتروني",
        subject: "طلب عرض تجريبي — أورا ريتيل"
      },
      footer: {
        desc: "نظام نقاط بيع عربي أولاً للمحلات الأردنية. مبني ليواصل البيع حين ينقطع الإنترنت.",
        product_h: "المنتج",
        company_h: "تواصل معنا",
        note_h: "يجدر معرفته",
        note1: "الأسعار المعروضة بالدينار الأردني تُدفع دفعة واحدة فقط — وليست اشتراكاً.",
        note2: "الفوترة الإلكترونية جاهزة ولم تُفعَّل بعد — راجع صفحة المزايا.",
        note3: "لا حاجة لحساب سحابي للمزامنة بين أجهزتك الخاصة.",
        rights: "أكشن أورا. جميع الحقوق محفوظة."
      }
    }
  };

  global.AURA_CONTENT = content;
  global.AURA_CONTACT = CONTACT;
  global.AURA_PRICING = PRICING;
})(window);
