export const legalEn = {
  privacy: {
    title: 'Privacy',
    updated: 'October 2026',
    draftNote: 'Draft pending lawyer review. This text may change before launch.',
    sections: [
      {
        h: 'Who we are',
        p: [
          'This website is run by الذئب الأسود لتصميم وتشغيل التطبيقات وبرامج الأعمال, trading as Action Aura, in Amman, Jordan.',
          'This page explains what this website collects and why, including the customer accounts you can create on it. It does not cover the Aura software itself, which has its own agreement.',
        ],
      },
      {
        h: 'What we collect',
        p: [
          'From the demo form on the contact page: your name, phone number, business type, business name, number of branches, your message, and the language of the page you were on.',
          'We also keep a salted hash of your IP address. A hash cannot be turned back into the address. We use it only to block abuse of the form. We also record the time the request arrived. A copy of each request is also sent to our own mailbox (info@actionaura.me) and to our own internal customer system, which only our staff can open.',
        ],
      },
      {
        h: 'Customer accounts',
        p: [
          'If you create an account, we keep your email address and your password. The password is stored only as a hash, so nobody at Action Aura can read it.',
        ],
      },
      {
        h: 'ID and commercial-registration photos',
        p: [
          'When one of our sellers registers a shop, the seller may collect photos of an ID document and of the commercial registration, to check who the customer is and to issue the subscription. These photos are stored encrypted, and only authorised staff can open them.',
        ],
      },
      {
        h: 'Why we collect it',
        p: [
          'To call you back about a demo, to run your account, and to check and issue your subscription. We do not use your details for anything else, and we do not sell or share them.',
        ],
      },
      {
        h: 'How long we keep it',
        p: [
          'Demo requests: 24 months, then deleted. This applies to the copies in our mailbox and in our internal customer system too.',
          'Accounts and ID or commercial-registration photos: for as long as you are a customer, plus 1 year. If a free trial is never followed by a payment, they are deleted 90 days after the trial ends.',
        ],
      },
      {
        h: 'Where it is hosted',
        p: [
          'This website is hosted by Cloudflare, which processes the data on our behalf.',
          'Account and shop data is kept on our servers in Germany (Oracle Cloud, Frankfurt), with encrypted backups on Cloudflare R2.',
        ],
      },
      {
        h: 'Analytics and cookies',
        p: [
          'We measure visits with cookieless analytics. It does not follow you from one site to another.',
          'When you sign in, we set one session cookie. It is HttpOnly, so scripts on the page cannot read it, and it is needed to keep you signed in. It expires on its own and is removed when you sign out. This website sets no tracking cookies. Your language choice may be remembered in your browser\'s local storage on this device only. It is not sent to us.',
        ],
      },
      {
        h: 'Turnstile',
        p: [
          'The contact page uses Cloudflare Turnstile, an anti-abuse check that tells people from automated programs. It is used only on the contact page.',
        ],
      },
      {
        h: 'Your rights',
        p: [
          'Under Jordan\'s Personal Data Protection Law of 2023 you may, within the limits of that law, ask to see your data, correct it, delete it, object to how it is used, or withdraw your consent.',
          'Write to info@actionaura.me and tell us the email address or phone number you used. We will answer your request.',
        ],
      },
    ],
  },
  terms: {
    title: 'Terms',
    updated: 'October 2026',
    draftNote: 'Draft. A lawyer must review this text before launch.',
    sections: [
      {
        h: 'What these terms cover',
        p: [
          'These terms cover your use of this website only. They do not cover the Aura software or its licence. The software is governed by its own agreement.',
        ],
      },
      {
        h: 'Content',
        p: [
          'We take care to keep this website accurate, but it is a general description of our products. It can change, and it may not be complete.',
        ],
      },
      {
        h: 'No prices or offers',
        p: [
          'Nothing on this website is a price, a quote or an offer. Pricing depends on your business. Ask us.',
        ],
      },
      {
        h: 'Links',
        p: [
          'This website may link to other websites. We do not control them and are not responsible for what they say or do.',
        ],
      },
      {
        h: 'Governing law',
        p: [
          'The laws of the Hashemite Kingdom of Jordan apply to your use of this website.',
        ],
      },
      {
        h: 'Contact',
        p: [
          'Questions about these terms: info@actionaura.me.',
        ],
      },
    ],
  },
} as const;
