import type { Dict } from './en';
import { retailAr } from './pages/retail.ar';
import { clinicAr } from './pages/clinic.ar';
import { aboutAr } from './pages/about.ar';
import { faqAr } from './pages/faq.ar';
import { contactAr } from './pages/contact.ar';
import { legalAr } from './pages/legal.ar';
import { accountAr } from './pages/account.ar';

export const ar: Dict = {
  meta: {
    home: {
      title: 'أكشن أورا | برامج إدارة المحلات في الأردن',
      description: 'أكشن أورا شركة برمجيات في عمّان. اكتشف أورا ريتيل، برنامج كاشير وإدارة محلات يعمل بدون إنترنت ويربط أجهزتك. احجز عرضًا تجريبيًا.',
    },
    retail: {
      title: 'Aura Retail | برنامج كاشير وإدارة محلات',
      description: 'كاشير على الكمبيوتر وتطبيق للمدير على الموبايل، يعمل بدون الإنترنت، مخزون، ديون الزبائن والموردين، تقارير، فروع متعددة وقارئ باركود.',
    },
    clinic: {
      title: 'Aura Clinic | برنامج إدارة العيادات', // AR: drafted, native check pending
      description: 'يغطي Aura Clinic المواعيد وملفات المرضى وخطط العلاج والفواتير للعيادات. مشفّر على جهاز العيادة ويعمل بدون الإنترنت.', // AR: drafted, native check pending
    },
    about: {
      title: 'من نحن والشركاء | أكشن أورا',
      description: 'تعرّف على أكشن أورا في عمّان، وكيف نعمل مع أصحاب المحلات والشركاء والمستثمرين.',
    },
    faq: {
      title: 'الأسئلة الشائعة | أكشن أورا', // AR: drafted, native check pending
      description: 'إجابات عن Aura Retail: العمل بدون الإنترنت، العربية، الأجهزة، الفروع، أمان البيانات، التركيب والتدريب. اسألنا عن الأسعار.', // AR: drafted, native check pending
    },
    contact: {
      title: 'احجز عرضًا تجريبيًا | أكشن أورا',
      description: 'احجز عرضًا تجريبيًا لـ Aura Retail أو تواصل معنا في عمّان على +962 78 006 9485.',
    },
    privacy: {
      title: 'سياسة الخصوصية | أكشن أورا',
      description: 'كيف تتعامل أكشن أورا مع البيانات التي ترسلها عبر هذا الموقع، ومدة الاحتفاظ بها، وكيف تطلب حذفها.', // AR: drafted, native check pending
    },
    terms: {
      title: 'شروط الاستخدام | أكشن أورا', // AR: drafted, native check pending
      description: 'شروط استخدام موقع أكشن أورا. تخص استخدام الموقع فقط، ولا تشمل ترخيص برنامج أورا.', // AR: drafted, native check pending
    },
    notFound: {
      title: 'الصفحة غير موجودة | أكشن أورا', // AR: drafted, native check pending
      description: 'هذه الصفحة غير موجودة. عُد إلى أكشن أورا.', // AR: drafted, native check pending
    },
    chooser: {
      title: 'أكشن أورا | العربية أو English', // AR: drafted, native check pending
      description: 'أكشن أورا، برامج للمحلات والعيادات من عمّان، الأردن. اختر العربية أو الإنجليزية.', // AR: drafted, native check pending
    },
  },
  nav: {
    brand: 'أكشن أورا',
    retail: 'أورا ريتيل',
    clinic: 'أورا كلينك',
    gym: 'أورا جيم',
    about: 'من نحن', // AR: drafted, native check pending
    demo: 'احجز عرضًا مجانيًا',
    switchTo: 'English',
    menu: 'القائمة', // AR: drafted, native check pending
    close: 'إغلاق', // AR: drafted, native check pending
    stripWa: 'واتساب',
    signIn: 'تسجيل الدخول',
  },
  cta: {
    whatsapp: 'راسلنا على واتساب',
    demo: 'احجز عرضًا مجانيًا',
    call: 'اتصل بنا',
    price: 'السعر حسب محلّك، اسألنا.',
    waText: 'مرحبًا أكشن أورا، أودّ معرفة المزيد عن أورا ريتيل.', // AR: drafted, native check pending
  },
  status: { available: 'متوفّر الآن', coming: 'قريبًا' },
  home: {
    hero: {
      title: 'كبّر شغلك، واشتغل بذكاء', // AR: drafted, native check pending
      lead: 'أكشن أورا نقطة البداية. أدِر عملك باحتراف، مع كاشير ومخزون وحسابات تبقى منظّمة حتى وأنت مرتاح.', // AR: drafted, native check pending
    },
    company: {
      title: 'أكشن أورا', accent: 'أورا',
      lead: 'شركة برمجيات أردنية تبني أنظمة تعمل كما تعمل المنشآت فعلًا.',
    },
    retail: {
      title: 'أورا ريتيل', accent: 'ريتيل',
      lead: 'الكاشير الذي يكمل البيع بدون إنترنت. قارئ باركود، ومخزون، وما لك وما عليك، وهاتف يعرض ما يجري على الكاشير.', // AR: drafted, native check pending
      link: 'تعرّف على أورا ريتيل', // AR: drafted, native check pending
    },
    // The three playable parts of the retail chapter (E2). Wording ported from the rejected look's i18n (copy rules
    // already applied there); only the three intro blocks (eyebrow/title/lead of till, the headings of offline/what) are new.
    till: {
      eyebrow: 'جرّب الكاشير', // AR: drafted, native check pending
      title: 'اضغط على الأصناف وشوف الفاتورة.', // AR: drafted, native check pending
      lead: 'اختر كم صنف، ادفع، وشوف البيعة بتوصل لموبايل المدير.', // AR: drafted, native check pending
      label: 'كاشير تجريبي مباشر', // AR: drafted, native check pending
      brand: 'أورا ريتيل',
      cashier: '· الكاشير 1',
      online: 'متصل',
      bill: 'الفاتورة',
      empty: 'الفاتورة فاضية. اضغط على صنف.', // AR: drafted, native check pending
      total: 'المجموع',
      currency: 'د.أ',
      pay: 'دفع نقدي',
      done: 'تمت البيعة',
      footnote: 'الأسعار في هذا المثال توضيحية.',
      phoneApp: 'تطبيق المدير',
      today: 'مبيعات اليوم',
      count: 'عدد البيعات: ',
      liveTotal: 'المجموع {n} د.أ', // AR: drafted, native check pending
      liveDone: 'تمت البيعة، {n} د.أ', // AR: drafted, native check pending
      items: [
        { name: 'مياه 1.5 لتر', price: '0.350' },
        { name: 'خبز عربي', price: '0.500' },
        { name: 'لبنة 500 غم', price: '1.750' },
        { name: 'شاي 100 كيس', price: '2.250' },
        { name: 'بيبسي 330 مل', price: '0.400' },
        { name: 'شيبس', price: '0.350' },
      ],
    },
    offline: {
      eyebrow: 'جرّبها بإيدك',
      title: 'اقطع النت بإيدك، وشوف شو بصير.',
      lead: 'اضغط على المفتاح. الكاشير بيضل يبيع، والمبيعات بتنحفظ على الجهاز. أول ما يرجع النت، بتوصل لموبايلك لحالها.', // AR: drafted, native check pending
      switchLabel: 'الإنترنت',
      on: 'شغّال',
      off: 'مقطوع',
      newSale: 'بيعة جديدة',
      note: 'هذا عرض توضيحي. البرنامج الحقيقي بيشتغل بنفس الطريقة.', // AR: drafted, native check pending
      chipOn: 'متصل · كل شي واصل',
      chipOff: 'بدون إنترنت · البيع شغّال',
      recent: 'آخر البيعات',
      empty: 'لسا ما في بيعات. اضغط بيعة جديدة.', // AR: drafted, native check pending
      queued: 'بانتظار',
      sent: 'تم الإرسال',
      badge: 'بانتظار الإرسال',
      lastUpdate: 'آخر تحديث: ',
      demoLabel: 'عرض الكاشير والموبايل', // AR: drafted, native check pending
      announceOff: 'الإنترنت مقطوع. الكاشير بيكمل البيع.', // AR: drafted, native check pending
      announceOn: 'الإنترنت رجع.', // AR: drafted, native check pending
      toastOne: 'وصلت بيعة وحدة',
      toastTwo: 'وصلت بيعتين',
      toastFew: 'وصلت {n} بيعات',
      toastMany: 'وصلت {n} بيعة',
    },
    what: {
      eyebrow: 'شو بيعمل',
      title: 'أربع أشياء بتمشّي المحل.',
      listLabel: 'أقسام البرنامج',
      items: [
        { tab: 'بيع', fact: 'اقرأ الباركود بالقارئ، والصنف بينزل على الفاتورة لحاله.', alt: 'شاشة البيع في أورا ريتيل: فاتورة بخمسة أصناف وأزرار الدفع، وبجانبها الأصناف.' },
        { tab: 'مخزون', fact: 'كل صنف وكميته قدامك، والصنف اللي قارب يخلص بينعلّم.', alt: 'قائمة المنتجات في أورا ريتيل مع الكميات، وصنف قاربت كميته على الانتهاء معلّم بتحذير.' },
        { tab: 'حسابات', fact: 'ما لك وما عليك: شو على الزبائن، وشو عليك للموردين.', alt: 'شاشة المستحقات في أورا ريتيل: أعمار الديون وجدول بأسماء الزبائن والأرصدة.' },
        { tab: 'فروع', fact: 'أكثر من فرع بنظام واحد، وموبايلك بيورجيك مبيعات كل فرع.', alt: 'شاشة اليوم في أورا ريتيل: بطاقة لكل فرع مع مبيعاته، ومخطط لآخر أربعة عشر يومًا.' },
      ],
    },
    // E3: clinic booking and gym check-in previews. Claims are only: appointments, patient files, treatment plans,
    // invoices (clinic); members, subscriptions, attendance check-in (gym). Names and treatments are made up.
    book: {
      eyebrow: 'جرّب الحجز', // AR: drafted, native check pending
      title: 'احجز موعدًا بضغطة وحدة.', // AR: drafted, native check pending
      lead: 'اضغط على وقت فاضي، وشوف الموعد بينحجز باسم مريض وملفه بيفتح بجانبه.', // AR: drafted, native check pending
      label: 'حجز مواعيد تجريبي', // AR: drafted, native check pending
      today: 'اليوم',
      count: 'مواعيد اليوم',
      free: 'متاح',
      bookFree: 'احجز الساعة {t}',
      booked: 'محجوز',
      fileTitle: 'ملف المريض',
      lastVisit: 'آخر زيارة',
      plan: 'خطة العلاج',
      announce: 'تم حجز الساعة {t} للمريض {name}، {tx}. مواعيد اليوم: {n}.', // AR: drafted, native check pending
      footnote: 'هذا عرض توضيحي.',
      patients: [
        { name: 'سارة', tx: 'تنظيف أسنان', last: 'قبل 6 أشهر', plan: 'الخطوة 1 من 2' },
        { name: 'أحمد', tx: 'فحص دوري', last: 'قبل 4 أشهر', plan: 'الخطوة 1 من 1' },
        { name: 'لينا', tx: 'حشوة', last: 'قبل أسبوعين', plan: 'الخطوة 2 من 3' },
        { name: 'خالد', tx: 'تبييض', last: 'قبل 3 أسابيع', plan: 'الخطوة 1 من 2' },
        { name: 'ريم', tx: 'متابعة تقويم', last: 'قبل شهر', plan: 'الخطوة 5 من 8' },
        { name: 'يوسف', tx: 'علاج عصب', last: 'قبل 10 أيام', plan: 'الخطوة 2 من 3' },
        { name: 'هنادي', tx: 'فحص أطفال', last: 'قبل سنة', plan: 'الخطوة 1 من 1' },
        { name: 'ليث', tx: 'تنظيف أسنان', last: 'قبل 5 أشهر', plan: 'الخطوة 1 من 2' },
      ],
    },
    checkin: {
      eyebrow: 'جرّب الدخول', // AR: drafted, native check pending
      title: 'اضغط على عضو وشوف شو بصير عند الباب.', // AR: drafted, native check pending
      lead: 'صاحب الاشتراك الساري بيدخل، والمنتهي بيرجع للاستقبال.', // AR: drafted, native check pending
      label: 'تسجيل دخول أعضاء تجريبي', // AR: drafted, native check pending
      soon: 'قريبًا',
      visits: 'زيارات اليوم',
      door: 'الباب',
      idle: 'اختر عضوًا',
      welcome: 'أهلين يا {name} · {days}',
      expiredMsg: 'الاشتراك منتهي · جدّد عند الاستقبال',
      expiredShort: 'منتهي',
      footnote: 'هذا عرض توضيحي.',
      members: [
        { name: 'عمر', days: 'باقي 12 يوم', ok: 'true' },
        { name: 'دانة', days: 'باقي 3 أيام', ok: 'true' },
        { name: 'كريم', days: 'منتهي', ok: 'false' },
      ],
    },
    clinic: {
      title: 'أورا كلينك', accent: 'كلينك',
      lead: 'المواعيد وملفات المرضى وخطط العلاج والفواتير، في النظام الهادئ نفسه.', // AR: drafted, native check pending
      link: 'تعرّف على أورا كلينك', // AR: drafted, native check pending
    },
    gym: {
      title: 'أورا جيم', accent: 'جيم',
      lead: 'الأعضاء والاشتراكات وتسجيل الحضور للأندية الرياضية والاستوديوهات. قيد التطوير لدى أكشن أورا.', // AR: drafted, native check pending
    },
    close: {
      title: 'حدّثنا عن محلّك.',
      lead: 'فريقنا في عمّان يركّب ويدرّب ويدعم.',
    },
  },
  footer: {
    tagline: 'كبّر شغلك · اشتغل بذكاء', // AR: drafted from the hero line, native check pending (the English slogan leaked onto /ar)
    products: 'المنتجات', // AR: drafted, native check pending
    company: 'الشركة', // AR: drafted, native check pending
    privacy: 'الخصوصية', // AR: drafted, native check pending
    terms: 'الشروط', // AR: drafted, native check pending
    faq: 'الأسئلة الشائعة', // AR: drafted, native check pending
    city: 'عمّان، الأردن', // AR: drafted, native check pending
    phone: '+962 78 006 9485', instagram: 'إنستغرام',
  },
  notFound: {
    title: 'هذه الصفحة ليست على الرف.',
    back: 'العودة إلى البداية', // AR: drafted, native check pending
  },
  chooser: {
    heading: 'اختر لغتك', // AR: drafted, native check pending
    ar: 'العربية',
    en: 'English',
    noscript: 'اختر اللغة من الأعلى. الرابطان يعملان بدون جافاسكربت.', // AR: drafted, native check pending
  },
  a11y: {
    skip: 'انتقل إلى المحتوى', // AR: drafted, native check pending
    jewel: 'شعار أكشن أورا',
    nav: 'القائمة الرئيسية', // AR: drafted, native check pending
  },
  pages: { retail: retailAr, clinic: clinicAr, about: aboutAr, faq: faqAr, contact: contactAr, legal: legalAr, account: accountAr },
};
