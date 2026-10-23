// Every visible English string. ar.ts must have exactly these keys (the type
// below enforces it at `astro check`; tests/unit/i18n.test.ts enforces it at
// test time). Claims come only from docs/messaging.md (traced to content.py).
import type { DeepStrings } from './types';
import { retailEn } from './pages/retail.en';
import { clinicEn } from './pages/clinic.en';
import { aboutEn } from './pages/about.en';
import { faqEn } from './pages/faq.en';
import { contactEn } from './pages/contact.en';
import { legalEn } from './pages/legal.en';

export const en = {
  meta: {
    home: {
      title: 'Action Aura | Retail Software from Amman, Jordan',
      description: 'Action Aura builds Aura Retail, POS and shop-management software that works offline and syncs your devices. Book a demo.',
    },
    retail: {
      title: 'Aura Retail | POS and Shop Management Software',
      description: 'Desktop till plus manager phone app. Works offline, syncs devices, tracks stock, customer and supplier debts, reports, multiple branches, barcode scanning.',
    },
    clinic: {
      title: 'Aura Clinic | Clinic Management Software',
      description: 'Aura Clinic covers appointments, patient files, treatment plans and billing for clinics. Encrypted on the clinic PC and works offline.',
    },
    about: {
      title: 'About and Partners | Action Aura',
      description: 'Meet Action Aura in Amman and learn how we work with shop owners, partners and investors.',
    },
    faq: {
      title: 'FAQ | Action Aura',
      description: 'Answers about Aura Retail: working offline, Arabic, hardware, branches, data safety, installation and training. Ask us about pricing.',
    },
    contact: {
      title: 'Book a Demo | Action Aura',
      description: 'Book a demo of Aura Retail or contact our Amman team on +962 78 006 9485.',
    },
    privacy: {
      title: 'Privacy Policy | Action Aura',
      description: 'How Action Aura handles the details you send through this website, how long we keep them, and how to ask for deletion.',
    },
    terms: {
      title: 'Terms of Use | Action Aura',
      description: 'Terms for using the Action Aura website. They cover website use only, not the Aura software licence.',
    },
    notFound: { title: 'Page not found | Action Aura', description: 'This page is not here. Go back to Action Aura.' },
    chooser: { title: 'Action Aura | العربية or English', description: 'Action Aura, retail and clinic software from Amman, Jordan. Choose Arabic or English.' },
  },
  nav: {
    brand: 'Action Aura', retail: 'Aura Retail', clinic: 'Aura Clinic', gym: 'Aura Gym', about: 'About',
    demo: 'Book a free demo', switchTo: 'العربية', menu: 'Menu', close: 'Close',
  },
  strip: { city: 'Amman, Jordan', whatsapp: 'WhatsApp', label: 'Contact' },
  cta: {
    whatsapp: 'Chat on WhatsApp', demo: 'Book a free demo', call: 'Call us',
    price: 'Pricing depends on your shop. Ask us.',
    waText: 'Hello Action Aura, I would like to know more about Aura Retail.',
  },
  status: { available: 'Available now', coming: 'Coming' },
  home: {
    hero: {
      eyebrow: 'Aura Retail · Available now',
      title: 'Try the till yourself.',
      lead: 'Tap the items and watch the bill add up in front of you. This is how Aura Retail works in your shop, on Windows and Android, even when the internet drops.',
      hint: 'Try it: tap any item',
    },
    till: {
      label: 'Live demo till',
      brand: 'Aura Retail',
      cashier: '· Till 1',
      online: 'Online',
      bill: 'Bill',
      empty: 'The bill is empty. Tap an item.',
      total: 'Total',
      currency: 'JOD',
      pay: 'Pay cash',
      done: 'Sale done',
      footnote: 'Prices in this example are for illustration.',
      phoneApp: 'Manager app',
      today: "Today's sales",
      count: 'Sales: ',
      liveTotal: 'Total {n} JOD',
      liveDone: 'Sale done, {n} JOD',
      items: [
        { name: 'Water 1.5 L', price: '0.350' },
        { name: 'Arabic bread', price: '0.500' },
        { name: 'Labneh 500 g', price: '1.750' },
        { name: 'Tea 100 bags', price: '2.250' },
        { name: 'Pepsi 330 ml', price: '0.400' },
        { name: 'Chips', price: '0.350' },
      ],
    },
    facts: [
      { title: 'Keeps selling without internet', body: 'Internet down? The till carries on, and the sales arrive when it returns.' },
      { title: 'Your phone sees the till', body: 'The manager app shows you what is happening in the shop while you are out.' },
      { title: 'A team in Amman', body: 'We install, we train, and we stay with you.' },
    ],
    offline: {
      eyebrow: 'Try it yourself',
      title: 'Cut the internet yourself, and see what happens.',
      lead: 'Flip the switch. The till keeps selling, and the sales are saved on the device. As soon as the internet is back, they reach your phone by themselves.',
      switchLabel: 'Internet',
      on: 'Online',
      off: 'Offline',
      newSale: 'New sale',
      note: 'This is a demo. The real program works the same way.',
      chipOn: 'Online · all sent',
      chipOff: 'No internet · still selling',
      recent: 'Recent sales',
      empty: 'No sales yet. Press New sale.',
      queued: 'Waiting',
      sent: 'Sent',
      badge: 'waiting to send',
      lastUpdate: 'Last update: ',
      demoLabel: 'Till and phone demo',
      announceOff: 'Internet is off. The till keeps selling.',
      announceOn: 'Internet is back.',
      toastOne: '1 sale arrived',
      toastTwo: '2 sales arrived',
      toastFew: '{n} sales arrived',
      toastMany: '{n} sales arrived',
    },
    company: {
      title: 'Action Aura', accent: 'Aura',
      lead: 'A Jordanian software company building systems that work the way businesses do.',
    },
    retail: {
      title: 'Aura Retail', accent: 'Retail',
      lead: 'The till that keeps selling offline. Barcode scanning, stock, receivables and payables, and a phone that mirrors the counter.',
      link: 'See Aura Retail',
    },
    clinic: {
      title: 'Aura Clinic', accent: 'Clinic',
      lead: 'Appointments, patient records, treatment plans and billing, in the same calm system.',
      link: 'See Aura Clinic',
    },
    gym: {
      title: 'Aura Gym', accent: 'Gym',
      lead: 'Members, plans and check-ins for gyms and studios. In the works at Action Aura.',
    },
    close: {
      title: 'Tell us about your shop.',
      lead: 'Our team in Amman installs, trains and supports.',
    },
  },
  footer: {
    products: 'Products', company: 'Company',
    privacy: 'Privacy', terms: 'Terms', faq: 'FAQ', city: 'Amman, Jordan', phone: '+962 78 006 9485', instagram: 'Instagram',
  },
  notFound: { title: "This page isn't on the shelf.", back: 'Back to the start' },
  chooser: {
    heading: 'Choose your language', ar: 'العربية', en: 'English',
    noscript: 'Choose a language above. Both links work without JavaScript.',
  },
  a11y: { skip: 'Skip to content', jewel: 'Action Aura mark', nav: 'Main' },
  pages: { retail: retailEn, clinic: clinicEn, about: aboutEn, faq: faqEn, contact: contactEn, legal: legalEn },
} as const;

export type Dict = DeepStrings<typeof en>;
