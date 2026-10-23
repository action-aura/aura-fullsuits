import type { ImageMetadata } from 'astro';
import posEn from '../assets/shots/pos-en.png';
import posAr from '../assets/shots/pos-ar.png';
import dashboardEn from '../assets/shots/dashboard-en.png';
import dashboardAr from '../assets/shots/dashboard-ar.png';
import reportsEn from '../assets/shots/reports-en.png';
import trialBalanceEn from '../assets/shots/trial-balance-en.png';
import productsEn from '../assets/shots/products-en.png';
import customersEn from '../assets/shots/customers-en.png';
import posNightEn from '../assets/shots/pos-night-en.png';
import sellAr from '../assets/shots/sell-ar.png';
import sellEn from '../assets/shots/sell-en.png';
import stockAr from '../assets/shots/stock-ar.png';
import stockEn from '../assets/shots/stock-en.png';
import owedAr from '../assets/shots/owed-ar.png';
import owedEn from '../assets/shots/owed-en.png';
import branchesAr from '../assets/shots/branches-ar.png';
import branchesEn from '../assets/shots/branches-en.png';

export type ShotKey = 'pos' | 'dashboard' | 'reports' | 'trialBalance' | 'products' | 'customers' | 'posNight';

export const SHOTS: Record<ShotKey, { en: ImageMetadata; ar: ImageMetadata | null; alt: { en: string; ar: string } }> = {
  pos: {
    en: posEn,
    ar: posAr,
    alt: { en: 'Aura Retail till, sale in progress', ar: 'كاشير أورا ريتيل أثناء عملية بيع' },
  },
  dashboard: {
    en: dashboardEn,
    ar: dashboardAr,
    alt: { en: 'Aura Retail dashboard', ar: 'لوحة معلومات أورا ريتيل' },
  },
  reports: {
    en: reportsEn,
    ar: null,
    alt: { en: 'Aura Retail reports screen', ar: 'شاشة التقارير في أورا ريتيل' },
  },
  trialBalance: {
    en: trialBalanceEn,
    ar: null,
    alt: { en: 'Aura Retail trial balance report', ar: 'تقرير ميزان المراجعة في أورا ريتيل' },
  },
  products: {
    en: productsEn,
    ar: null,
    alt: { en: 'Aura Retail products list', ar: 'قائمة المنتجات في أورا ريتيل' },
  },
  customers: {
    en: customersEn,
    ar: null,
    alt: { en: 'Aura Retail customers list', ar: 'قائمة العملاء في أورا ريتيل' },
  },
  posNight: {
    en: posNightEn,
    ar: null,
    alt: { en: 'Aura Retail till in the night theme', ar: 'كاشير أورا ريتيل بالسمة الليلية' },
  },
};

export const isShotKey = (k: string): k is ShotKey => k in SHOTS;

// Home "what it does" section: demo-data crops of the real app, one per language, same 2.2:1 frame. Order matches
// i18n home.what.items. Alt text lives in the i18n files (it is copy), not here.
export const WHAT_SHOTS: { en: ImageMetadata; ar: ImageMetadata }[] = [
  { en: sellEn, ar: sellAr },
  { en: stockEn, ar: stockAr },
  { en: owedEn, ar: owedAr },
  { en: branchesEn, ar: branchesAr },
];
