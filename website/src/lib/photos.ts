import type { ImageMetadata } from 'astro';
import groceryEn from '../assets/photos/retail-grocery-en.jpg';
import groceryAr from '../assets/photos/retail-grocery-ar.jpg';
import phoneShopEn from '../assets/photos/retail-phone-shop-en.jpg';
import phoneShopAr from '../assets/photos/retail-phone-shop-ar.jpg';

// Composited illustrations (tools/composite_screens.py): an AI-generated shop counter with a real Aura screen on its
// display, one EN and one AR screen each so the Arabic page shows the Arabic UI (design-system.md section 7).
// Alt text and captions live in the page copy (i18n/pages/retail.*.ts, key `photos`), so the dictionary tests scan
// them for banned claims, em-dashes and Arabic-Indic digits like every other string on the site.
export type PhotoKey = 'grocery' | 'phoneShop';

export const PHOTOS: Record<PhotoKey, { en: ImageMetadata; ar: ImageMetadata }> = {
  grocery: { en: groceryEn, ar: groceryAr },
  phoneShop: { en: phoneShopEn, ar: phoneShopAr },
};

export const isPhotoKey = (k: string): k is PhotoKey => k in PHOTOS;
