import type { Dict } from './en';
import { retailAr } from './pages/retail.ar';
import { clinicAr } from './pages/clinic.ar';
import { aboutAr } from './pages/about.ar';
import { faqAr } from './pages/faq.ar';
import { contactAr } from './pages/contact.ar';
import { legalAr } from './pages/legal.ar';

export const ar: Dict = {
  meta: {
    home: {
      title: 'أكشن أورا | برامج إدارة المحلات في الأردن',
      description: 'أكشن أورا شركة برمجيات في عمّان. اكتشف أورا ريتيل، برنامج كاشير وإدارة محلات يعمل بدون إنترنت ويربط أجهزتك. احجز عرضًا تجريبيًا.',
    },
    retail: {
      title: 'أورا ريتيل | برنامج كاشير وإدارة محلات',
      description: 'كاشير على الكمبيوتر وتطبيق للمدير على الموبايل، يعمل بدون الإنترنت، مخزون، ديون الزبائن والموردين، تقارير، فروع متعددة وقارئ باركود.',
    },
    clinic: {
      title: 'أورا كلينك | برنامج إدارة العيادات', // AR: drafted, native check pending
      description: 'يغطي أورا كلينك المواعيد وملفات المرضى وخطط العلاج والفواتير للعيادات. مشفّر على جهاز العيادة ويعمل بدون الإنترنت.', // AR: drafted, native check pending
    },
    about: {
      title: 'من نحن والشركاء | أكشن أورا',
      description: 'تعرّف على أكشن أورا في عمّان، وكيف نعمل مع أصحاب المحلات والشركاء والمستثمرين.',
    },
    faq: {
      title: 'الأسئلة الشائعة | أكشن أورا', // AR: drafted, native check pending
      description: 'إجابات عن أورا ريتيل: العمل بدون الإنترنت، العربية، الأجهزة، الفروع، أمان البيانات، التركيب والتدريب. اسألنا عن الأسعار.', // AR: drafted, native check pending
    },
    contact: {
      title: 'احجز عرضًا تجريبيًا | أكشن أورا',
      description: 'احجز عرضًا تجريبيًا لـ أورا ريتيل أو تواصل معنا في عمّان على +962 78 006 9485.',
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
  },
  strip: { city: 'عمّان، الأردن', whatsapp: 'واتساب', label: 'التواصل' }, // AR: drafted, native check pending
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
      eyebrow: 'أورا ريتيل · متوفّر الآن',
      title: 'جرّب الكاشير بنفسك.',
      lead: 'اضغط على الأصناف وشوف الفاتورة تنحسب قدامك. هيك بتشتغل أورا ريتيل في محلّك، على ويندوز وأندرويد، وحتى لو قطع النت.', // AR: drafted, native check pending
      hint: 'جرّب: اضغط على أي صنف', // AR: drafted, native check pending
    },
    till: {
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
    facts: [
      { title: 'يكمل البيع بدون إنترنت', body: 'النت قطع؟ الكاشير بيكمل، والمبيعات بتوصل لما يرجع.' }, // AR: drafted, native check pending
      { title: 'موبايلك بيشوف الكاشير', body: 'تطبيق المدير بيورجيك شو بصير بالمحل وإنت برّا.' }, // AR: drafted, native check pending
      { title: 'فريق في عمّان', body: 'بنركّب، وبندرّب، وبنضل معك.' }, // AR: drafted, native check pending
    ],
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
    company: {
      title: 'أكشن أورا', accent: 'أورا',
      lead: 'شركة برمجيات أردنية تبني أنظمة تعمل كما تعمل المنشآت فعلًا.',
    },
    retail: {
      title: 'أورا ريتيل', accent: 'ريتيل',
      lead: 'الكاشير الذي يكمل البيع بدون إنترنت. قارئ باركود، ومخزون، وما لك وما عليك، وهاتف يعرض ما يجري على الكاشير.', // AR: drafted, native check pending
      link: 'تعرّف على أورا ريتيل', // AR: drafted, native check pending
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
  pages: { retail: retailAr, clinic: clinicAr, about: aboutAr, faq: faqAr, contact: contactAr, legal: legalAr },
};
