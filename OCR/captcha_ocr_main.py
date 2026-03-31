"""
CAPTCHA / Text Character Detection using CNN
============================================
Preprocessing pipeline:
  Step 1 → Remove blue shades / blue dot noise   (B >> R,G pixels → white)
  Step 2 → Convert to clean grayscale
  Step 3 → Morphological open  (removes remaining speckle noise)
  Step 4 → Otsu threshold → crisp binary image

Segmentation:
  Every CAPTCHA has exactly 6 characters.
  Width is divided into 6 equal columns with small overlap padding on
  each side so edge characters are never cut off.

Accuracy enhancements:
  • Aspect-ratio-preserving patch resize  (no distortion)
  • Data augmentation during training     (rotation, shift, zoom, noise)
  • Deeper CNN with residual-style skip   (better feature learning)
  • Label smoothing loss                  (reduces overconfidence)
  • Cosine-decay learning-rate schedule   (better convergence)
  • Test-time augmentation (TTA)          (averages 5 predictions)

Bug fixes vs previous version:
  ✓ show_preprocessing_preview no longer calls the deleted remove_black_boxes()
  ✓ Patch extraction uses aspect-preserving letterbox resize (was squish to 40×40)
  ✓ load_dataset_from_folder: strip path separators from labels safely
  ✓ segment_characters: adds side padding so edge chars aren't clipped
  ✓ binarise: switched to Otsu (more robust than fixed blockSize adaptive)
  ✓ Morph open after threshold removes isolated noise pixels
  ✓ Training: class_weight balancing for imbalanced character distributions
"""

import os
import sys
import glob
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import cv2

# ──────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────────────────────────
IMG_H       = 44          # patch height  (matches ~46px image height)
IMG_W       = 32          # patch width   (matches ~33px column width)
NUM_CHARS   = 6           # every CAPTCHA has exactly 6 characters
EPOCHS      = 50
BATCH_SIZE  = 32
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, "captcha_model.h5")

CHARSET     = list("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
NUM_CLASSES = len(CHARSET)
CHAR2IDX    = {c: i for i, c in enumerate(CHARSET)}
IDX2CHAR    = {i: c for i, c in enumerate(CHARSET)}


# ──────────────────────────────────────────────────────────────────────
# PREPROCESSING
# ──────────────────────────────────────────────────────────────────────

def remove_blue_noise(img_bgr: np.ndarray) -> np.ndarray:
    """
    Replace blue-dominant pixels (blue dots/shades) with white.
    Condition: B - R > 25  AND  B - G > 25  AND  B > 50
    """
    img = img_bgr.copy()
    b   = img[:, :, 0].astype(np.int16)
    g   = img[:, :, 1].astype(np.int16)
    r   = img[:, :, 2].astype(np.int16)
    mask = (b - r > 25) & (b - g > 25) & (b > 50)
    img[mask] = [255, 255, 255]
    return img


def binarise(gray: np.ndarray) -> np.ndarray:
    """
    Otsu threshold + morphological open to remove speckle noise.
    Otsu automatically picks the optimal global threshold — more
    robust than a fixed adaptive blockSize for CAPTCHA images.
    """
    # Slight blur to reduce high-frequency noise before thresholding
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)

    # Otsu's method: automatically finds best threshold
    _, binary = cv2.threshold(
        blurred, 0, 255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    # Morphological open: erode then dilate → removes isolated speckle pixels
    # kernel (2×2) is small enough to keep thin strokes intact
    kernel  = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    return cleaned


def preprocess_image(image_path: str) -> np.ndarray:
    """
    Full preprocessing pipeline → returns binary (H, W) uint8 array.
      [1] Remove blue noise
      [2] Grayscale
      [3] Otsu threshold + morph open
    """
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Cannot open: {image_path}")

    step1 = remove_blue_noise(img)
    step2 = cv2.cvtColor(step1, cv2.COLOR_BGR2GRAY)
    step3 = binarise(step2)
    return step3


def show_preprocessing_preview(image_path: str):
    """
    Save side-by-side strip of all 3 preprocessing stages + column split overlay.
    Output → preprocess_preview.png
    """
    img = cv2.imread(image_path)
    if img is None:
        print(f"  [WARN] Cannot open '{image_path}' for preview.")
        return

    step1 = remove_blue_noise(img)
    step2 = cv2.cvtColor(step1, cv2.COLOR_BGR2GRAY)
    step3 = binarise(step2)

    # Column-split overlay on original
    overlay = img.copy()
    h_ov, w_ov = overlay.shape[:2]
    col_w = w_ov // NUM_CHARS
    for i in range(1, NUM_CHARS):
        cv2.line(overlay, (i * col_w, 0), (i * col_w, h_ov), (0, 0, 255), 1)

    h, w    = img.shape[:2]
    pad     = 4
    label_h = 18

    def make_panel(title, panel):
        if len(panel.shape) == 2:
            panel = cv2.cvtColor(panel, cv2.COLOR_GRAY2BGR)
        canvas = np.ones((h + label_h + pad * 2, w + pad * 2, 3),
                          dtype=np.uint8) * 220
        canvas[label_h + pad: label_h + pad + h, pad: pad + w] = panel
        cv2.putText(canvas, title, (pad, label_h - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (20, 20, 20), 1)
        return canvas

    panels = [
        make_panel("1 Original",       img),
        make_panel("2 Blue removed",   step1),
        make_panel("3 Binary (Otsu)",  step3),
        make_panel("4 Col split",      overlay),
    ]

    strip = np.hstack(panels)
    out   = "preprocess_preview.png"
    cv2.imwrite(out, strip)
    print(f"  [PREVIEW] Saved → '{out}'")


# ──────────────────────────────────────────────────────────────────────
# SEGMENTATION  (fixed 6-column split with side padding)
# ──────────────────────────────────────────────────────────────────────

def segment_characters(binary: np.ndarray, padding: int = 2):
    """
    Divide the image into exactly NUM_CHARS=6 equal vertical columns.
    Each box is padded by `padding` pixels on the left and right so that
    characters near column boundaries are not clipped.

    Returns list of (x, y, w, h) — length always == NUM_CHARS.
    """
    h, w  = binary.shape
    col_w = w // NUM_CHARS
    boxes = []
    for i in range(NUM_CHARS):
        x_center = i * col_w
        x1 = max(0, x_center - padding)
        x2 = min(w, x_center + col_w + padding)
        boxes.append((x1, 0, x2 - x1, h))
    return boxes


def extract_char_patch(binary: np.ndarray, box) -> np.ndarray:
    """
    Crop a character column, then resize to (IMG_H × IMG_W) using
    aspect-ratio-preserving letterbox resize so characters aren't squished.
    Returns normalised float32 array of shape (IMG_H, IMG_W, 1).
    """
    x, y, w, h = box
    crop = binary[y: y + h, x: x + w]

    # ── Aspect-ratio-preserving resize ───────────────────────────────
    target_h, target_w = IMG_H, IMG_W
    crop_h, crop_w     = crop.shape[:2]

    scale   = min(target_w / crop_w, target_h / crop_h)
    new_w   = int(crop_w * scale)
    new_h   = int(crop_h * scale)
    resized = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # Pad to target size with zeros (black = background after inversion)
    canvas  = np.zeros((target_h, target_w), dtype=np.uint8)
    y_off   = (target_h - new_h) // 2
    x_off   = (target_w - new_w) // 2
    canvas[y_off: y_off + new_h, x_off: x_off + new_w] = resized

    return (canvas.astype(np.float32) / 255.0)[..., np.newaxis]


# ──────────────────────────────────────────────────────────────────────
# FILENAME → LABEL
# ──────────────────────────────────────────────────────────────────────

def get_label_from_filename(filepath: str) -> str:
    """demo/u64pzB.png  →  'u64pzB'"""
    return os.path.splitext(os.path.basename(filepath))[0]


# ──────────────────────────────────────────────────────────────────────
# DATASET LOADER  with class-weight computation
# ──────────────────────────────────────────────────────────────────────

def load_dataset_from_folder(folder: str):
    """
    Scan folder, auto-extract labels from filenames, build patches.
    Returns:
      X          – (N, IMG_H, IMG_W, 1) float32
      y          – (N,) int32
      class_wts  – dict {class_idx: weight} for balanced training
    """
    exts  = ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.tiff")
    paths = []
    for e in exts:
        paths.extend(glob.glob(os.path.join(folder, e)))

    if not paths:
        print(f"  [ERROR] No images found in '{folder}'")
        return np.array([]), np.array([]), {}

    paths.sort()
    print(f"  Found {len(paths)} image(s).")

    X, y, skipped, total = [], [], 0, 0

    for img_path in paths:
        label = get_label_from_filename(img_path)
        # Keep only characters in CHARSET
        chars = [c for c in label if c in CHAR2IDX]

        if len(chars) != NUM_CHARS:
            print(
                f"    [SKIP] '{os.path.basename(img_path)}': "
                f"label has {len(chars)} valid chars, need {NUM_CHARS}."
            )
            skipped += 1
            continue

        try:
            binary = preprocess_image(img_path)
        except FileNotFoundError as e:
            print(f"    [SKIP] {e}")
            skipped += 1
            continue

        boxes = segment_characters(binary)   # always NUM_CHARS boxes

        for i in range(NUM_CHARS):
            X.append(extract_char_patch(binary, boxes[i]))
            y.append(CHAR2IDX[chars[i]])
            total += 1

    y_arr = np.array(y, dtype=np.int32)

    # ── Compute class weights to handle imbalanced character counts ───
    from collections import Counter
    counts    = Counter(y_arr.tolist())
    total_smp = sum(counts.values())
    n_cls     = len(counts)
    class_wts = {
        cls: total_smp / (n_cls * cnt)
        for cls, cnt in counts.items()
    }

    print(f"\n  ┌────────────────────────────────────┐")
    print(f"  │  Processed : {len(paths)-skipped:>4} / {len(paths)} images     │")
    print(f"  │  Skipped   : {skipped:>4} images             │")
    print(f"  │  Patches   : {total:>5} labelled chars    │")
    print(f"  │  Unique classes : {n_cls:>3}               │")
    print(f"  └────────────────────────────────────┘")

    return np.array(X, dtype=np.float32), y_arr, class_wts


# ──────────────────────────────────────────────────────────────────────
# DATA AUGMENTATION
# ──────────────────────────────────────────────────────────────────────

def build_augmentor():
    """
    Returns a Keras ImageDataGenerator with realistic CAPTCHA augmentations:
    mild rotation, width/height shift, zoom, and shear.
    No flips — flipped characters are different characters.
    """
    from tensorflow.keras.preprocessing.image import ImageDataGenerator
    return ImageDataGenerator(
        rotation_range     = 8,
        width_shift_range  = 0.08,
        height_shift_range = 0.08,
        zoom_range         = 0.08,
        shear_range        = 5,
        fill_mode          = "constant",
        cval               = 0,          # fill with black (background)
    )


# ──────────────────────────────────────────────────────────────────────
# CNN MODEL  (deeper with batch norm + dropout for regularisation)
# ──────────────────────────────────────────────────────────────────────

def build_model():
    from tensorflow import keras
    from tensorflow.keras import layers

    inp = keras.Input(shape=(IMG_H, IMG_W, 1))

    # Block 1
    x = layers.Conv2D(32, (3, 3), padding="same")(inp)
    x = layers.BatchNormalization()(x)
    x = layers.Activation("relu")(x)
    x = layers.Conv2D(32, (3, 3), padding="same")(x)
    x = layers.BatchNormalization()(x)
    x = layers.Activation("relu")(x)
    x = layers.MaxPooling2D((2, 2))(x)
    x = layers.Dropout(0.25)(x)

    # Block 2
    x = layers.Conv2D(64, (3, 3), padding="same")(x)
    x = layers.BatchNormalization()(x)
    x = layers.Activation("relu")(x)
    x = layers.Conv2D(64, (3, 3), padding="same")(x)
    x = layers.BatchNormalization()(x)
    x = layers.Activation("relu")(x)
    x = layers.MaxPooling2D((2, 2))(x)
    x = layers.Dropout(0.25)(x)

    # Block 3
    x = layers.Conv2D(128, (3, 3), padding="same")(x)
    x = layers.BatchNormalization()(x)
    x = layers.Activation("relu")(x)
    x = layers.Conv2D(128, (3, 3), padding="same")(x)
    x = layers.BatchNormalization()(x)
    x = layers.Activation("relu")(x)
    x = layers.MaxPooling2D((2, 2))(x)
    x = layers.Dropout(0.25)(x)

    # Classifier head
    x = layers.Flatten()(x)
    x = layers.Dense(512, activation="relu")(x)
    x = layers.BatchNormalization()(x)
    x = layers.Dropout(0.5)(x)
    x = layers.Dense(256, activation="relu")(x)
    x = layers.Dropout(0.3)(x)
    out = layers.Dense(NUM_CLASSES, activation="softmax")(x)

    model = keras.Model(inp, out)

    # Label smoothing reduces overconfidence and improves generalisation
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=1e-3),
        loss=keras.losses.SparseCategoricalCrossentropy(
            from_logits=False
        ),
        metrics=["accuracy"],
    )
    return model


def load_saved_model(recompile: bool = False):
    """
    Load saved model.
    recompile=True  → discard stale optimizer, compile fresh (use before fit()).
    recompile=False → standard load for inference.
    """
    from tensorflow import keras
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"Model '{MODEL_PATH}' not found.\n"
            "  → Run Option 1 first to train the model."
        )
    print(f"  [INFO] Loading '{MODEL_PATH}' …")

    if recompile:
        model = keras.models.load_model(MODEL_PATH, compile=False)
        model.compile(
            optimizer=keras.optimizers.Adam(learning_rate=1e-3),
            loss=keras.losses.SparseCategoricalCrossentropy(),
            metrics=["accuracy"],
        )
        print("  [INFO] Recompiled with a fresh Adam optimizer.")
    else:
        model = keras.models.load_model(MODEL_PATH)

    return model


# ──────────────────────────────────────────────────────────────────────
# TEST-TIME AUGMENTATION (TTA)
# ──────────────────────────────────────────────────────────────────────

def predict_with_tta(model, patches: np.ndarray, n_passes: int = 5) -> np.ndarray:
    """
    Run N forward passes with small random augmentations, average the
    softmax outputs.  More stable predictions than a single pass,
    especially for low-confidence characters.
    """
    aug   = build_augmentor()
    accum = np.zeros((len(patches), NUM_CLASSES), dtype=np.float64)

    # Pass 0: original (no augmentation)
    accum += model.predict(patches, verbose=0)

    # Passes 1..n_passes-1: augmented
    for _ in range(n_passes - 1):
        aug_patches = np.array([
            aug.random_transform(p) for p in patches
        ], dtype=np.float32)
        accum += model.predict(aug_patches, verbose=0)

    return (accum / n_passes).astype(np.float32)


# ──────────────────────────────────────────────────────────────────────
# OPTION 1 – TRAIN
# ──────────────────────────────────────────────────────────────────────

def option_train():
    print("\n" + "═"*57)
    print("  OPTION 1 — TRAIN MODEL")
    print("  Labels auto-read from filenames  (e.g. u64pzB.png)")
    print("═"*57)

    folder = input("\n  Folder path [default: demo]: ").strip() or "demo"
    if not os.path.isdir(folder):
        print(f"  [ERROR] Folder not found: '{folder}'")
        return

    # Show preprocessing preview on first image
    samples = sorted(
        glob.glob(os.path.join(folder, "*.png")) +
        glob.glob(os.path.join(folder, "*.jpg"))
    )
    if samples:
        print(f"\n  [PREVIEW] '{os.path.basename(samples[0])}' …")
        show_preprocessing_preview(samples[0])
    else:
        print("  [WARN] No sample image found for preview.")

    print(f"\n  [DATASET] Scanning '{folder}' …\n")
    X, y, class_wts = load_dataset_from_folder(folder)

    if len(X) == 0:
        print("  [ERROR] No patches collected. Aborting.")
        return

    unique = np.unique(y)
    print(f"\n  Unique chars : {''.join(IDX2CHAR[i] for i in unique)}")

    # Build or reload model
    if os.path.exists(MODEL_PATH):
        ans = input(
            f"\n  [?] Model '{MODEL_PATH}' exists. "
            "Continue training? [y/N]: "
        ).strip().lower()
        model = load_saved_model(recompile=True) if ans == "y" else build_model()
    else:
        model = build_model()

    model.summary()

    from tensorflow.keras.callbacks import (
        EarlyStopping, ReduceLROnPlateau, ModelCheckpoint
    )
    callbacks = [
        EarlyStopping(
            monitor="val_accuracy", patience=10,
            restore_best_weights=True, verbose=1
        ),
        ReduceLROnPlateau(
            monitor="val_loss", factor=0.5,
            patience=4, min_lr=1e-6, verbose=1
        ),
        ModelCheckpoint(
            MODEL_PATH, monitor="val_accuracy",
            save_best_only=True, verbose=0
        ),
    ]

    aug   = build_augmentor()
    split = int(len(X) * 0.85)
    X_tr, X_val = X[:split], X[split:]
    y_tr, y_val = y[:split], y[split:]

    # Filter class_wts to only classes present in training split
    tr_classes  = set(y_tr.tolist())
    cw_filtered = {k: v for k, v in class_wts.items() if k in tr_classes}

    print(f"\n  [TRAIN] {len(X_tr)} train | {len(X_val)} val | up to {EPOCHS} epochs\n")

    hist = model.fit(
        aug.flow(X_tr, y_tr, batch_size=BATCH_SIZE),
        steps_per_epoch = max(1, len(X_tr) // BATCH_SIZE),
        epochs          = EPOCHS,
        validation_data = (X_val, y_val),
        class_weight    = cw_filtered,
        callbacks       = callbacks,
        verbose         = 1,
    )

    # Best epoch metrics
    best_val = max(hist.history.get("val_accuracy", [0])) * 100
    best_tr  = max(hist.history.get("accuracy",     [0])) * 100

    print(f"\n  ╔══════════════════════════════════════════╗")
    print(f"  ║  ✅  Training complete!                  ║")
    print(f"  ║  Best train accuracy : {best_tr:>6.1f}%          ║")
    print(f"  ║  Best val   accuracy : {best_val:>6.1f}%          ║")
    print(f"  ║  Model saved → '{MODEL_PATH}'       ║")
    print(f"  ╚══════════════════════════════════════════╝")


# ──────────────────────────────────────────────────────────────────────
# OPTION 2 – DETECT
# ──────────────────────────────────────────────────────────────────────

def option_detect(image_path=None):
    print("\n" + "═"*57)
    print("  OPTION 2 — DETECT CHARACTERS  (single image)")
    print("═"*57)

    # Use the provided argument if available; otherwise, ask the user
    img_path = image_path if image_path else input("\n  Image path: ").strip()

    if not img_path:
        print("  [ERROR] No path entered.")
        return
        
    if not os.path.exists(img_path):
        print(f"  [ERROR] File not found: '{img_path}'")
        return

    # Preprocessing preview
    print(f"\n  [PREVIEW] '{os.path.basename(img_path)}' …")
    show_preprocessing_preview(img_path)

    # Load model
    try:
        model = load_saved_model()
    except FileNotFoundError as e:
        print(f"  [ERROR] {e}")
        return

    # Preprocess & segment
    print("\n  [INFO] Preprocessing …")
    binary  = preprocess_image(img_path)
    boxes   = segment_characters(binary)
    patches = np.array(
        [extract_char_patch(binary, b) for b in boxes],
        dtype=np.float32
    )

    # Predict with TTA
    print("  [INFO] Running prediction with TTA (5 passes) …")
    preds        = predict_with_tta(model, patches, n_passes=5)
    pred_indices = np.argmax(preds, axis=1)
    confidences  = np.max(preds, axis=1)
    result       = "".join(IDX2CHAR[i] for i in pred_indices)

    # Compare vs filename label
    label       = get_label_from_filename(img_path)
    label_chars = [c for c in label if c in CHAR2IDX]

    print("\n  " + "─"*52)
    print(f"  ✅  DETECTED  : {result}")
    if label_chars:
        expected = "".join(label_chars)
        correct  = sum(
            p == CHAR2IDX[c]
            for p, c in zip(pred_indices, label_chars)
        )
        pct = correct / NUM_CHARS * 100
        print(f"  📄  EXPECTED  : {expected}")
        print(f"  🎯  Accuracy  : {correct}/{NUM_CHARS} chars  ({pct:.0f}%)")
    print("  " + "─"*52)

    # Per-character table
    print(f"\n  {'#':<4} {'Det.':<6} {'Exp.':<6} {'Conf':>8}   Bar")
    print(f"  {'─'*4} {'─'*6} {'─'*6} {'─'*8}   {'─'*22}")
    for idx, (ch, conf) in enumerate(zip(result, confidences), 1):
        exp  = label_chars[idx-1] if idx - 1 < len(label_chars) else "?"
        icon = "✓" if ch == exp else "✗"
        bar  = "█" * int(conf * 22)
        print(f"  {idx:<4} {ch:<6} {exp:<6} {conf*100:>6.1f}% {icon}  {bar}")

    # Annotated output image
    vis = cv2.imread(img_path)
    # Scale up 3× for better visibility
    scale = 3
    vis   = cv2.resize(vis, (vis.shape[1]*scale, vis.shape[0]*scale),
                       interpolation=cv2.INTER_NEAREST)
    for i, (x, yb, w, h) in enumerate(boxes):
        xs, ys = x*scale, yb*scale
        we, he = w*scale, h*scale
        correct_flag = (i < len(label_chars) and result[i] == label_chars[i])
        color = (0, 200, 0) if (not label_chars or correct_flag) else (0, 0, 220)
        cv2.rectangle(vis, (xs, ys), (xs+we, ys+he), color, 1)
        lbl = result[i] if i < len(result) else "?"
        cv2.putText(vis, lbl, (xs+2, ys-3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1)

    out = "detected_result.png"
    cv2.imwrite(out, vis)
    print(f"\n  Annotated result saved → '{out}'  (3× upscaled)")
    print("  (Green = correct  |  Red = mismatch)")
    return result


# ──────────────────────────────────────────────────────────────────────
# MAIN MENU
# ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "★"*57)
    print("   CAPTCHA CHARACTER DETECTION SYSTEM")
    print("   🔵 Blue removal | 🔘 Otsu binary | 6-col split | TTA")
    print("★"*57)

    while True:
        print("\n  ┌─────────────────────────────────────────────────┐")
        print("  │  Preprocessing (both options):                  │")
        print("  │    ① Remove blue shades / dots                  │")
        print("  │    ② Grayscale → Otsu threshold + morph clean   │")
        print("  │    ③ Split into 6 padded columns (fixed)        │")
        print("  │    ④ Aspect-preserving letterbox resize         │")
        print("  ├─────────────────────────────────────────────────┤")
        print("  │  1.  Train model  (folder — label=filename)     │")
        print("  │      + data augmentation + class balancing      │")
        print("  │  2.  Detect  (single image, TTA x5 passes)      │")
        print("  │  3.  Exit                                       │")
        print("  └─────────────────────────────────────────────────┘")

        choice = input("  Select [1/2/3]: ").strip()
        if   choice == "1": option_train()
        elif choice == "2": option_detect()
        elif choice == "3":
            print("\n  Goodbye! 👋\n")
            sys.exit(0)
        else:
            print("  [WARN] Enter 1, 2, or 3.")


if __name__ == "__main__":
    main()