# KrishiConnect AI - ML Model Training Script
# Trains RandomForest on ml_training_data_clean.xlsx
# Output: backend/models/disease_model.pkl

import pandas as pd
import numpy as np
import pickle
import os
import sys
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score

sys.stdout.reconfigure(encoding='utf-8')

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_PATH = os.path.join(BASE_DIR, 'ml_training_data_clean.xlsx')
MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')
MODEL_PATH = os.path.join(MODEL_DIR, 'disease_model.pkl')

os.makedirs(MODEL_DIR, exist_ok=True)

print("=" * 50)
print("KrishiConnect AI - ML Model Training")
print("=" * 50)

# Load data
df = pd.read_excel(DATA_PATH)
print(f"\nLoaded {len(df)} samples")
print(f"Crops: {df['crop'].nunique()}")
print(f"Diseases: {df['disease'].nunique()}")
print(f"has_disease distribution:\n{df['has_disease'].value_counts().to_dict()}")

# Encode categorical features
label_encoders = {}
categorical_cols = ['crop', 'disease', 'season', 'region_type']

for col in categorical_cols:
    le = LabelEncoder()
    df[col + '_enc'] = le.fit_transform(df[col].astype(str))
    label_encoders[col] = le
    print(f"  {col}: {len(le.classes_)} classes")

# Feature columns
feature_cols = [
    'crop_enc', 'disease_enc', 'season_enc', 'region_type_enc',
    'month', 'avg_temp_C', 'avg_humidity_pct', 'total_rainfall_mm',
    'soil_moisture_pct', 'wind_speed_kmh', 'consecutive_wet_days'
]

X = df[feature_cols].fillna(0)
y = df['has_disease'].astype(int)

# Split
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

print(f"\nTraining set: {len(X_train)} samples")
print(f"Test set: {len(X_test)} samples")

# Train
print("\nTraining RandomForest...")
model = RandomForestClassifier(
    n_estimators=150,
    max_depth=12,
    min_samples_split=5,
    min_samples_leaf=2,
    random_state=42,
    n_jobs=-1
)
model.fit(X_train, y_train)

# Evaluate
y_pred = model.predict(X_test)
accuracy = accuracy_score(y_test, y_pred)
print(f"\nAccuracy: {accuracy:.4f} ({accuracy*100:.1f}%)")
print("\nClassification Report:")
print(classification_report(y_test, y_pred, target_names=['No Disease', 'Disease']))

# Feature importance
importances = dict(zip(feature_cols, model.feature_importances_))
print("\nFeature Importance:")
for feat, imp in sorted(importances.items(), key=lambda x: -x[1]):
    bar = '#' * int(imp * 50)
    print(f"  {feat:25s} {imp:.3f} {bar}")

# Save model + encoders + metadata
model_data = {
    'model': model,
    'label_encoders': label_encoders,
    'feature_cols': feature_cols,
    'accuracy': accuracy,
    'crops': list(label_encoders['crop'].classes_),
    'diseases': list(label_encoders['disease'].classes_),
    'seasons': list(label_encoders['season'].classes_),
    'regions': list(label_encoders['region_type'].classes_),
    'trained_at': pd.Timestamp.now().isoformat(),
    'samples': len(df),
}

with open(MODEL_PATH, 'wb') as f:
    pickle.dump(model_data, f)

size_kb = os.path.getsize(MODEL_PATH) / 1024
print(f"\nModel saved to: {MODEL_PATH}")
print(f"Model size: {size_kb:.0f} KB")
print(f"\nKnown crops: {model_data['crops']}")
print(f"Known diseases: {model_data['diseases']}")
print("\nDone!")
