#[allow(dead_code)]
fn spare() -> i32 {
    1
}

fn grade(n: i32) -> &'static str {
    if n < 0 {
        "negative"
    } else if n == 0 {
        "zero"
    } else if n < 10 {
        "small"
    } else if n < 100 {
        "medium"
    } else if n < 1000 {
        "large"
    } else if n < 10000 {
        "huge"
    } else if n < 100000 {
        "vast"
    } else {
        "enormous"
    }
}

fn main() {
    println!("{}", grade(42));
}
