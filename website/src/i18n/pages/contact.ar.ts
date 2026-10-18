import type { DeepStrings } from '../types';
import type { contactEn } from './contact.en';

export const contactAr: DeepStrings<typeof contactEn> = {
  title: 'احجز عرضًا مجانيًا',
  lead: 'دقيقة واحدة. سنتواصل معك لنرتّب موعدًا يناسبك.',
  form: {
    name: 'الاسم',
    phone: 'رقم الهاتف أو واتساب',
    phoneHelp: 'سنتصل بك على هذا الرقم.', // AR: drafted, native check pending
    type: 'نوع النشاط', // AR: drafted, native check pending
    typeOptions: {
      shop: 'محل', // AR: drafted, native check pending
      clinic: 'عيادة', // AR: drafted, native check pending
      other: 'غير ذلك',
    },
    businessName: 'اسم النشاط', // AR: drafted, native check pending
    branches: 'عدد الفروع',
    branchesOptions: {
      one: '1',
      few: '2 إلى 3', // AR: drafted, native check pending
      many: 'أكثر',
    },
    message: 'رسالتك', // AR: drafted, native check pending
    optional: 'اختياري', // AR: drafted, native check pending
    send: 'احجز عرضًا مجانيًا',
    sendWa: 'راسلنا على واتساب', // AR: drafted, native check pending
    sending: 'جارٍ الإرسال', // AR: drafted, native check pending
    success: 'شكرًا لك، وصلنا طلبك. سنتصل بك على الرقم الذي أدخلته.',
    errorNetwork: 'تعذّر إرسال طلبك. تحقق من الاتصال بالإنترنت وحاول مرة أخرى.', // AR: drafted, native check pending
    errorServer: 'حدث خطأ من جهتنا. حاول مرة أخرى بعد قليل.', // AR: drafted, native check pending
    errorRate: 'محاولات كثيرة. انتظر قليلًا ثم حاول مرة أخرى.', // AR: drafted, native check pending
    errorChallenge: 'أكمل خطوة التحقق أعلاه، ثم أرسل مرة أخرى.', // AR: drafted, native check pending
    noscript: 'هذا النموذج يحتاج إلى جافاسكربت. راسلنا على واتساب أو اتصل على +962 78 006 9485.', // AR: drafted, native check pending
    noJsError: 'لم نتمكن من إرسال النموذج من هذه الصفحة. راسلنا على واتساب أو اتصل بنا وسنحجز لك العرض.', // AR: drafted, native check pending
    errors: {
      nameRequired: 'يرجى إدخال اسمك.', // AR: drafted, native check pending
      nameLong: 'الاسم طويل جدًا.', // AR: drafted, native check pending
      phoneRequired: 'يرجى إدخال رقم الهاتف.', // AR: drafted, native check pending
      phoneInvalid: 'رقم الهاتف غير صحيح.', // AR: drafted, native check pending
      tooLong: 'النص طويل جدًا.', // AR: drafted, native check pending
    },
  },
  direct: {
    title: 'أو تواصل معنا مباشرة', // AR: drafted, native check pending
    whatsapp: '+962 78 006 9485',
    call: '+962 78 006 9485',
    email: 'info@actionaura.me',
    office: 'مجمع الريان، شارع الإذاعة والتلفزيون، المقابلين، عمّان',
    hours: 'من الأحد إلى الخميس، 9:00 إلى 17:00', // OWNER: confirm hours
  },
};
