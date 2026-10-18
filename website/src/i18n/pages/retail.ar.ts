import type { DeepStrings } from '../types';
import type { retailEn } from './retail.en';

export const retailAr: DeepStrings<typeof retailEn> = {
  hero: {
    status: 'متاح الآن', // AR: drafted, native check pending
    title: 'الكاشير الذي يواصل البيع بلا إنترنت.', // AR: drafted, native check pending
    accent: 'بلا إنترنت', // AR: drafted, native check pending
    lead: 'أورا ريتيل نظام كاشير وإدارة محلات بالعربية أولًا، من أكشن أورا في عمّان. يعمل على كمبيوتر المحل وعلى هاتفك، ويتزامن بينهما عبر شبكة المحل، ويستمر في البيع عند انقطاع الإنترنت.',
  },
  rail: {
    title: 'يوم صاحب المحل', // AR: drafted, native check pending
    moments: [
      {
        title: 'افتح الكاشير', // AR: drafted, native check pending
        line: 'ابدأ اليوم برصيد نقدي افتتاحي، وكل بيع بعده يُحسب على هذا الرصيد.', // AR: drafted, native check pending
        shot: 'pos',
        alt: 'شاشة كاشير أورا ريتيل جاهزة لأول بيع في اليوم', // AR: drafted, native check pending
      },
      {
        title: 'امسح وبِع', // AR: drafted, native check pending
        line: 'بيع كامل من لوحة المفاتيح مع قارئ باركود، وتعليق الفواتير، وفحص السعر، والدفع نقدًا أو بطاقة أو كليك أو تحويل أو على الحساب.', // AR: drafted, native check pending
        shot: 'products',
        alt: 'قائمة أصناف أورا ريتيل مع الباركود والأسعار', // AR: drafted, native check pending
      },
      {
        title: 'ينقطع الإنترنت', // AR: drafted, native check pending
        line: 'الكاشير يكمل البيع والطباعة على كمبيوتر المحل، والمزامنة تكتمل وحدها عند عودة الاتصال.',
        shot: 'posNight',
        alt: 'كاشير أورا ريتيل بالمظهر الداكن أثناء عملية بيع', // AR: drafted, native check pending
      },
      {
        title: 'هاتفك يعكس ما على الطاولة', // AR: drafted, native check pending
        line: 'الكمبيوتر والهاتف يتعرّفان على بعضهما عبر شبكة المحل ويتزامنان تلقائيًا.',
        shot: 'dashboard',
        alt: 'لوحة أورا ريتيل تعرض مبيعات اليوم', // AR: drafted, native check pending
      },
      {
        title: 'آخر اليوم', // AR: drafted, native check pending
        line: 'شغّل تقارير X وZ وطابق النقد، وأي فرق يحتاج موافقتك.',
        shot: 'reports',
        alt: 'شاشة تقارير أورا ريتيل بأرقام نهاية اليوم', // AR: drafted, native check pending
      },
      {
        title: 'الدفاتر', // AR: drafted, native check pending
        line: 'كل بيع يُقيَّد آليًا في ميزان المراجعة والدخل والميزانية والتدفقات النقدية.',
        shot: 'trialBalance',
        alt: 'تقرير ميزان المراجعة في أورا ريتيل', // AR: drafted, native check pending
      },
    ],
  },
  features: {
    title: 'كل ما يحتاجه المحل', // AR: drafted, native check pending
    items: [
      {
        title: 'أثناء البيع',
        line: 'بيع كامل من لوحة المفاتيح، قارئ باركود، تعليق الفواتير، فحص السعر، وكل طرق الدفع.', // AR: drafted, native check pending
      },
      {
        title: 'المخزون',
        line: 'الأصناف والباركود، الموردون وأوامر الشراء، والمرتجعات.',
      },
      {
        title: 'ما لك وما عليك',
        line: 'حسابات آجلة للعملاء والموردين، كشوف، أعمار ديون، حدود ائتمان، شيكات، ونقاط ولاء وعروض.',
      },
      {
        title: 'آخر اليوم',
        line: 'رصيد افتتاحي، تقارير X وZ، ومطابقة النقد، وكل بيع يُقيَّد في دفاترك.', // AR: drafted, native check pending
      },
      {
        title: 'الفروع والأجهزة',
        line: 'الأجهزة تتعرّف على بعضها عبر شبكة المحل، والمخزون ينتقل بين الفروع.', // AR: drafted, native check pending
      },
      {
        title: 'أدوار الموظفين', // AR: drafted, native check pending
        line: 'أدوار المالك والمدير والكاشير والمحاسب، مع سجل تدقيق يبيّن من فعل ماذا.', // AR: drafted, native check pending
      },
    ],
  },
  works: {
    title: 'يعمل مع الأجهزة التي لديك', // AR: drafted, native check pending
    line: 'قارئات الباركود وطابعات الإيصالات تتصل بكمبيوتر المحل.', // AR: drafted, native check pending
  },
  jordan: {
    title: 'جاهز للأردن',
    items: [
      'المبالغ حتى الفلس، بثلاث خانات عشرية', // AR: drafted, native check pending
      'ضريبة مبيعات 16% افتراضيًا',
      'الدفع عبر كليك', // AR: drafted, native check pending
      'واجهة عربية كاملة من اليمين إلى اليسار',
    ],
  },
  run: {
    title: 'ما تحتاجه للتشغيل',
    line: 'كمبيوتر ويندوز للمحل، وهاتف أندرويد إن أردت، وشبكة المحل. نحن نركّب وننقل بياناتك وندرّب فريقك.',
  },
  cta: {
    title: 'حدّثنا عن محلّك.',
    lead: 'فريقنا في عمّان يركّب ويدرّب ويدعم. السعر حسب محلّك، اسألنا.',
  },
};
