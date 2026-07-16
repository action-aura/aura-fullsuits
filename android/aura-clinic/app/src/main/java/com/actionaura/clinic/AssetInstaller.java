package com.actionaura.clinic;

import android.content.Context;
import android.content.res.AssetManager;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * Extracts the bundled web assets (static/, templates/, config.json) from the APK's
 * assets/bundle/ into the app's private files dir on first run, and re-extracts after
 * an app update (keyed by versionCode). The writable data dir is left untouched, so
 * the user's data survives updates. Pure platform glue — no business logic.
 */
public final class AssetInstaller {

    private static final String ASSET_ROOT = "bundle";   // assets/bundle/**
    private static final String DEST_DIR = "bundle";      // <filesDir>/bundle

    private AssetInstaller() {}

    /** Install assets if missing or stale (version changed). Returns the bundle dir path. */
    public static String installIfNeeded(Context ctx, int versionCode) throws IOException {
        File bundleDir = new File(ctx.getFilesDir(), DEST_DIR);
        File marker = new File(bundleDir, ".version");

        String want = String.valueOf(versionCode);
        String have = readMarker(marker);
        if (bundleDir.exists() && want.equals(have)) {
            return bundleDir.getAbsolutePath();   // already current
        }

        deleteRecursive(bundleDir);
        bundleDir.mkdirs();
        copyAssetDir(ctx.getAssets(), ASSET_ROOT, bundleDir);
        writeMarker(marker, want);
        return bundleDir.getAbsolutePath();
    }

    private static void copyAssetDir(AssetManager am, String assetPath, File destDir) throws IOException {
        String[] children = am.list(assetPath);
        if (children == null || children.length == 0) {
            // It's a file (or empty) — copy as file.
            copyAssetFile(am, assetPath, destDir);
            return;
        }
        if (!destDir.exists()) destDir.mkdirs();
        for (String child : children) {
            String childAsset = assetPath + "/" + child;
            String[] grand = am.list(childAsset);
            File childDest = new File(destDir, child);
            if (grand != null && grand.length > 0) {
                copyAssetDir(am, childAsset, childDest);          // directory
            } else {
                copyAssetFile(am, childAsset, childDest);          // file
            }
        }
    }

    private static void copyAssetFile(AssetManager am, String assetPath, File dest) throws IOException {
        File parent = dest.getParentFile();
        if (parent != null && !parent.exists()) parent.mkdirs();
        try (InputStream in = am.open(assetPath); OutputStream out = new FileOutputStream(dest)) {
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) != -1) out.write(buf, 0, n);
        }
    }

    private static String readMarker(File marker) {
        try (InputStream in = new java.io.FileInputStream(marker)) {
            byte[] b = new byte[32];
            int n = in.read(b);
            return n > 0 ? new String(b, 0, n).trim() : "";
        } catch (IOException e) {
            return "";
        }
    }

    private static void writeMarker(File marker, String value) throws IOException {
        try (OutputStream out = new FileOutputStream(marker)) {
            out.write(value.getBytes());
        }
    }

    private static void deleteRecursive(File f) {
        if (f == null || !f.exists()) return;
        File[] kids = f.listFiles();
        if (kids != null) for (File k : kids) deleteRecursive(k);
        //noinspection ResultOfMethodCallIgnored
        f.delete();
    }
}
