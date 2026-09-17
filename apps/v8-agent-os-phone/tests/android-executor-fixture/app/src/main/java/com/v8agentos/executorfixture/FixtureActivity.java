package com.v8agentos.executorfixture;

import android.app.Activity;
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
    setContentView(layout);
  }
  @Override protected void onNewIntent(android.content.Intent intent) {
    super.onNewIntent(intent);
    if ("local_click".equals(intent.getStringExtra("fixtureAction"))) increment.performClick();
  }
}
