// Every visible English string. ar.ts must have exactly these keys (the type
// below enforces it at `astro check`; tests/unit/i18n.test.ts enforces it at
// test time). Claims come only from docs/messaging.md (traced to content.py).
type DeepStrings<T> = { [K in keyof T]: T[K] extends string ? string : DeepStrings<T[K]> };

export const en = {
  meta: {
    home: {
      title: 'Action Aura | Retail Software from Amman, Jordan',
      description: 'Action Aura builds Aura Retail, POS and shop-management software that works offline and syncs your devices. Book a demo.',
    },
    notFound: { title: 'Page not found | Action Aura', description: 'This page is not here. Go back to Action Aura.' },
    chooser: { title: 'Action Aura | العربية or English', description: 'Choose your language: Arabic or English.' },
  },
  nav: {
    brand: 'Action Aura', retail: 'Aura Retail', clinic: 'Aura Clinic', gym: 'Aura Gym', about: 'About',
    demo: 'Book a free demo', switchTo: 'العربية', menu: 'Menu', close: 'Close',
  },
  cta: {
    whatsapp: 'Chat on WhatsApp', demo: 'Book a free demo', call: 'Call us',
    price: 'Pricing depends on your shop. Ask us.',
    waText: 'Hello Action Aura, I would like to know more about Aura Retail.',
  },
  status: { available: 'Available now', coming: 'Coming' },
  home: {
    hero: {
      title: "Your shop keeps selling, even when the internet doesn't.",
      lead: 'Till, stock and books for shops and showrooms. It runs on the counter and on your phone, works offline, and syncs when you are back.',
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
    tagline: 'Go Bigger · Go Smarter', products: 'Products', company: 'Company',
    privacy: 'Privacy', terms: 'Terms', faq: 'FAQ', city: 'Amman, Jordan', phone: '+962 78 006 9485',
  },
  notFound: { title: "This page isn't on the shelf.", back: 'Back to the start' },
  chooser: { heading: 'Choose your language', ar: 'العربية', en: 'English' },
  a11y: { skip: 'Skip to content', jewel: 'Action Aura mark' },
} as const;

export type Dict = DeepStrings<typeof en>;
