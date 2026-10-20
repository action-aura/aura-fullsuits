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
    tagline: 'Go Bigger · Go Smarter',
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
