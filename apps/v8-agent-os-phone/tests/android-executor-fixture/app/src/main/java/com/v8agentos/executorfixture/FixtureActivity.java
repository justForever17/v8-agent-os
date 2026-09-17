package com.v8agentos.executorfixture;

import android.app.Activity;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Synthetic screen only: no network, files, contacts, accounts or other apps. */
public class FixtureActivity extends Activity {
  private int count = 0;
  private Button increment;
  @Override public void onCreate(Bundle state) {
    super.onCreate(state);
    getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    LinearLayout layout = new LinearLayout(this);
    layout.setOrientation(LinearLayout.VERTICAL);
    layout.setPadding(32, 80, 32, 32);
    TextView title = new TextView(this); title.setText("V8 Executor Synthetic Fixture"); title.setTextSize(22); layout.addView(title);
    TextView value = new TextView(this); value.setText("Count: 0"); value.setTextSize(22); layout.addView(value);
    increment = new Button(this); increment.setText("Increment counter"); increment.setOnClickListener(v -> value.setText("Count: " + (++count))); layout.addView(increment);
    Button noop = new Button(this); noop.setText("No effect (driver can accept)"); noop.setOnClickListener(v -> {}); layout.addView(noop);
    EditText input = new EditText(this); input.setHint("Synthetic text only"); input.setSingleLine(true); layout.addView(input);
    Button drift = new Button(this); drift.setText("Change fixture scene"); drift.setOnClickListener(v -> { title.setText("Changed fixture scene"); increment.setEnabled(false); }); layout.addView(drift);
    Button reset = new Button(this); reset.setText("Reset fixture"); reset.setOnClickListener(v -> { count = 0; value.setText("Count: 0"); title.setText("V8 Executor Synthetic Fixture"); increment.setEnabled(true); input.setText(""); }); layout.addView(reset);
    Button canvas = new Button(this); canvas.setText("Canvas-only scene"); canvas.setOnClickListener(v -> showCanvas()); layout.addView(canvas);
    setContentView(layout);
    applyFixtureIntent(getIntent());
  }
  @Override protected void onNewIntent(android.content.Intent intent) {
    super.onNewIntent(intent);
    setIntent(intent);
    applyFixtureIntent(intent);
  }
  private void applyFixtureIntent(android.content.Intent intent) {
    if ("local_click".equals(intent.getStringExtra("fixtureAction"))) increment.performClick();
    if ("canvas".equals(intent.getStringExtra("fixtureAction"))) {
      String nonce = intent.getStringExtra("fixtureSceneNonce");
      if (nonce == null || !nonce.matches("[a-zA-Z0-9_-]{1,80}")) throw new IllegalArgumentException("fixture_scene_nonce_required");
      setContentView(new CanvasFixture(nonce));
    }
    if ("secure_on".equals(intent.getStringExtra("fixtureAction"))) getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
    if ("secure_off".equals(intent.getStringExtra("fixtureAction"))) getWindow().clearFlags(WindowManager.LayoutParams.FLAG_SECURE);
    if ("landscape".equals(intent.getStringExtra("fixtureAction"))) setRequestedOrientation(android.content.pm.ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE);
    if ("portrait".equals(intent.getStringExtra("fixtureAction"))) setRequestedOrientation(android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT);
  }
  private void showCanvas() { setContentView(new CanvasFixture(java.util.UUID.randomUUID().toString())); }
  /** Pixels expose the target/result; deliberately has no virtual accessibility node map. */
  private class CanvasFixture extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final String sceneNonce;
    private int taps = 0, swipes = 0, eventSeq = 0;
    private float downX, downY;
    CanvasFixture(String nonce) {
      super(FixtureActivity.this); sceneNonce = nonce;
      setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
      recordCounters();
    }
    private void recordCounters() {
      android.util.Log.i("V8ExecutorTarget", "canvas_scene=" + sceneNonce + ";canvas_event=" + eventSeq
          + ";canvas_taps=" + taps + ";canvas_swipes=" + swipes);
    }
    @Override protected void onDraw(Canvas canvas) {
      canvas.drawColor(Color.WHITE);
      paint.setColor(Color.BLACK); paint.setTextSize(42);
      canvas.drawText("Synthetic pixels only", 40, 180, paint);
      canvas.drawText("Taps: " + taps + "  Swipes: " + swipes, 40, 250, paint);
      paint.setColor(Color.rgb(30, 100, 210));
      canvas.drawRect(getWidth() * .25f, getHeight() * .35f, getWidth() * .75f, getHeight() * .65f, paint);
      paint.setColor(Color.WHITE); canvas.drawText("Tap / swipe here", getWidth() * .27f, getHeight() * .5f, paint);
    }
    @Override public boolean onTouchEvent(MotionEvent event) {
      if (event.getActionMasked() == MotionEvent.ACTION_DOWN) { downX = event.getX(); downY = event.getY(); return true; }
      if (event.getActionMasked() == MotionEvent.ACTION_UP) {
        if (downX >= getWidth() * .25f && downX < getWidth() * .75f && downY >= getHeight() * .35f && downY < getHeight() * .65f) {
          if (Math.hypot(event.getX() - downX, event.getY() - downY) > 50) swipes++; else taps++;
          eventSeq++;
          recordCounters();
          invalidate();
        }
        return true;
      }
      return true;
    }
  }
}
